import time
import types
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select, update

from enterprise_data_platform.aggregation import AggregateRequest, Measure
from enterprise_data_platform.backends import eval_filter
from enterprise_data_platform.context import ExecutionContext
from enterprise_data_platform.connectors import ConnectorCapabilities, ConnectorRouter, SourceRegistration, SQLConnector
from enterprise_data_platform.control_models import ClientRegistration
from enterprise_data_platform.cursor import EncryptedCursorCodec
from enterprise_data_platform.durable import SQLCatalog, SQLControlState, SQLPolicyEngine, jobs
from enterprise_data_platform.execution import GovernedService
from enterprise_data_platform.governor import ResourceGovernor, Overloaded
from enterprise_data_platform.ingestion import IngestionBridge, IngestionEvent
from enterprise_data_platform.jobs import DurableQueue
from enterprise_data_platform.models import AccessPolicy, Capability, DataProduct, FieldDefinition, Principal, RetrieveRequest, SourceBinding, StructuredQueryRequest
from enterprise_data_platform.planner import GovernedPlanner
from enterprise_data_platform.services import AccessDenied
from test_durable_security import store
from test_platform_core import product, allow_policy, principal


def runtime_service(store):
    md = MetaData()
    table = Table('facts', md, Column('id', Integer, primary_key=True), Column('tenant', String), Column('amount', Integer))
    md.create_all(store.engine)
    with store.engine.begin() as conn:
        conn.execute(table.insert(), [{'id':1,'tenant':'a','amount':10},{'id':2,'tenant':'a','amount':20},{'id':3,'tenant':'b','amount':999}])
    p = DataProduct(id='facts', display_name='Facts', version='1', status='active',
        source=SourceBinding(connector='sql', source_id='source', environment='test', object_name='facts'),
        identity_fields=['id'], tenant_field='tenant', capabilities={Capability.QUERY, Capability.AGGREGATE, Capability.EXACT_COUNT},
        fields=[FieldDefinition(name='id', data_type='int', sortable=True, filterable=True),
            FieldDefinition(name='tenant', data_type='string', filterable=True), FieldDefinition(name='amount', data_type='int', filterable=True)])
    catalog, policies, control = SQLCatalog(store), SQLPolicyEngine(store), SQLControlState(store)
    catalog.put(p)
    policies.put(AccessPolicy(id='allow', effect='allow', operations=p.capabilities))
    control.clients.put(ClientRegistration(id='app',display_name='App',owner='team',allowed_datasets={'facts'},allowed_capabilities=p.capabilities))
    router = ConnectorRouter(store, None, factories={'test': lambda r,s: SQLConnector(store.engine, ConnectorCapabilities(aggregation=True))})
    router.registry.put(SourceRegistration(id='source',kind='test',secret_ref='file://dsn'))
    service = GovernedService(store=store,control=control,governor=ResourceGovernor(store),planner=GovernedPlanner(),
        catalog=catalog,policies=policies,cursor_codec=EncryptedCursorCodec(b'q'*32),structured=router)
    actor = Principal(subject='user',client_id='app',tenant='a',attributes={'oauth_scope':'edp:query edp:aggregate edp:exact_count'})
    return service, actor


def test_governed_runtime_rechecks_durable_policy_and_client(store):
    service, actor = runtime_service(store)
    assert service.query(actor,'facts',StructuredQueryRequest()).returned_rows == 2
    second = GovernedService(store=store,control=SQLControlState(store),governor=ResourceGovernor(store),planner=GovernedPlanner(),
        catalog=SQLCatalog(store),policies=SQLPolicyEngine(store),cursor_codec=EncryptedCursorCodec(b'q'*32),structured=service.structured)
    service.policies.put(AccessPolicy(id='deny',effect='deny',operations={Capability.QUERY}))
    with pytest.raises(AccessDenied):
        second.query(actor,'facts',StructuredQueryRequest())
    with pytest.raises(AccessDenied):
        second.aggregate(actor.model_copy(update={'attributes':{}}),'facts',AggregateRequest(measures=[Measure(name='s',function='sum',field='amount')]))


def test_aggregate_pushdown_respects_tenant_and_separate_count_permission(store):
    service, actor = runtime_service(store)
    req = AggregateRequest(group_by=['tenant'],measures=[Measure(name='total',function='sum',field='amount')])
    assert service.aggregate(actor,'facts',req).rows == [{'tenant':'a','total':30}]
    service.policies.put(AccessPolicy(id='deny-count',effect='deny',operations={Capability.EXACT_COUNT}))
    with pytest.raises(AccessDenied):
        service.aggregate(actor,'facts',AggregateRequest(measures=[Measure(name='n',function='count')]))
    service.policies.put(AccessPolicy(id='hide',effect='allow',denied_fields={'amount'},operations={Capability.AGGREGATE}))
    with pytest.raises(AccessDenied):
        service.aggregate(actor,'facts',req)


def test_publication_checkpoint_never_advances_past_unqueued_events(store):
    queue = DurableQueue(store,capacity=1)
    bridge = IngestionBridge(queue)
    def event(n):
        return IngestionEvent(dataset_id='data',dataset_version='1',index_version='index',partition='p0',
            sequence=n,event_id=str(n),operation='upsert',record_id=str(n),source_version=str(n))
    with pytest.raises(Overloaded):
        bridge.publish_page([event(1),event(2)],stream='snapshot',resume_token={'key':2},expected_revision=0)
    assert store.list('source_offsets') == []
    with store.engine.connect() as conn:
        assert conn.execute(select(jobs)).first() is None
    result = bridge.publish_page([event(1)],stream='snapshot',resume_token={'key':1},expected_revision=0)
    assert result['resume_token'] == {'key':1} and result['revision'] == 1
    assert store.get('source_offsets', result['checkpoint_id'])['payload']['resume_token'] == {'key':1}


def test_deadline_fallback_is_explicit_and_plan_changes_request():
    p=product(); p.retrieval.allow_keyword_fallback=True
    from enterprise_data_platform.policy import PolicyEngine
    decision=PolicyEngine([allow_policy()]).evaluate(principal=principal(),product=p,operation=Capability.RETRIEVE)
    plan=GovernedPlanner('Configured name').plan(p,decision,Capability.RETRIEVE,
        ExecutionContext(deadline=time.monotonic()+.5),request=RetrieveRequest(query='term'))
    assert plan.engine_name == 'Configured name'
    assert plan.request_updates == {'mode':'keyword'}
    assert 'vector_ann' not in plan.stages and 'final_authorization' in plan.stages


@pytest.mark.parametrize('expr', [
    {'not':{'field':'x','op':'eq','value':1}},
    {'not':{'field':'x','op':'in','value':[1,None]}},
    {'field':'x','op':'neq','value':1},
])
def test_null_filters_fail_closed_consistently(expr):
    assert not eval_filter({'x':None},expr)
