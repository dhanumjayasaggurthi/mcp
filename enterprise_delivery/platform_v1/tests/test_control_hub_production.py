"""UI-facing durable API and data-plane regression contracts."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import json
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Column,Integer,MetaData,Table,Text,select,event
from sqlalchemy.dialects import postgresql
from enterprise_data_platform.api import create_app
from enterprise_data_platform.context import execution_context
from enterprise_data_platform.control_models import AgentRegistration,ClientRegistration
from enterprise_data_platform.durable import SQLCatalog,SQLControlState,SQLPolicyEngine,objects
from enterprise_data_platform.jobs import DurableQueue
from enterprise_data_platform.models import Capability,Principal,RetrieveRequest,SortField,ProductStatus
from enterprise_data_platform.mcp_transport import register_mcp
from enterprise_data_platform.onboarding import index_plan
from enterprise_data_platform.sqlalchemy_backend import SQLAlchemyStructuredBackend
from enterprise_data_platform.services import CapabilityUnavailable
from test_durable_security import store
from test_production_boundaries import runtime_service
from test_platform_core import build_service,product,principal
from test_native_contracts import native_product


def app_for(store):
    service,actor=runtime_service(store)
    def resolve(req):
        token=req.headers.get('authorization')
        if token not in {'Bearer admin','Bearer reader','Bearer agent'}:raise HTTPException(401,'unauthorized')
        return actor.model_copy(update={'groups':{'data-platform-admin'} if token=='Bearer admin' else set(),
            'agent_id':'agent' if token=='Bearer agent' else None})
    app=create_app(service=service,catalog=service.catalog,policies=service.policies,control_state=service.control,
        principal_resolver=resolve,control_admin_check=lambda p:'data-platform-admin' in p.groups)
    return app,service,actor


def test_control_endpoints_reject_reader_and_allow_bounded_admin_reads(store):
    app,service,actor=app_for(store);client=TestClient(app)
    for path in ['/v1/control/forms','/v1/control/session','/v1/control/audit','/v1/control/jobs','/v1/control/history/datasets/facts']:
        assert client.get(path).status_code==401
        assert client.get(path,headers={'Authorization':'Bearer reader'}).status_code==403
        assert client.get(path,headers={'Authorization':'Bearer admin'}).status_code==200
    headers={'Authorization':'Bearer admin'}
    first=client.get('/v1/control/audit?limit=2',headers=headers).json()
    second=client.get('/v1/control/audit',params={'limit':2,'cursor':first['next_cursor']},headers=headers).json()
    assert not {r['id'] for r in first['events']} & {r['id'] for r in second['events']}
    assert client.get('/v1/control/audit?cursor=bad',headers=headers).status_code==400
    assert client.get('/v1/control/audit?limit=100000',headers=headers).status_code==422
    body={'dataset_id':'facts','operation':'query','principal':actor.model_dump(mode='json')}
    assert client.post('/v1/control/simulate',json=body,headers=headers).json()['allowed']
    body['principal']['client_id']='unregistered'
    assert not client.post('/v1/control/simulate',json=body,headers=headers).json()['allowed']


def test_registry_pagination_and_conflicts(store):
    app,service,_=app_for(store);c=TestClient(app);h={'Authorization':'Bearer admin'}
    for n in range(3):service.control.clients.put(ClientRegistration(id=f'client-{n}',display_name='Client',owner='owner'))
    first=c.get('/v1/control/clients?limit=2',headers=h).json()
    second=c.get('/v1/control/clients',params={'after':first['next_after'],'limit':2},headers=h).json()
    assert not {r['id'] for r in first['clients']} & {r['id'] for r in second['clients']}
    item=first['clients'][0]
    assert c.put('/v1/control/clients/'+item['id'],json=item,headers=h).status_code==200
    assert c.put('/v1/control/clients/'+item['id'],json=item,headers=h).status_code==409


def test_keyset_uses_nonnull_tuple_and_preserves_nullable_mixed_order():
    t=Table('chunks',MetaData(),Column('doc',Text,nullable=False),Column('idx',Integer,nullable=False),Column('optional',Text))
    for direction,symbol in [('asc','>'),('desc','<')]:
        where=SQLAlchemyStructuredBackend._keyset_after(t,[SortField(field='doc',direction=direction),SortField(field='idx',direction=direction)],{'doc':'z','idx':50})
        sql=str(where.compile(dialect=postgresql.dialect()))
        assert f'(chunks.doc, chunks.idx) {symbol}' in sql and 'IS NULL' not in sql
    mixed=SQLAlchemyStructuredBackend._keyset_after(t,[SortField(field='doc'),SortField(field='optional',direction='desc')],{'doc':'x','optional':'y'})
    sql=str(mixed.compile(dialect=postgresql.dialect()))
    assert 'chunks.doc IS NULL' not in sql and 'chunks.optional IS NULL' in sql


def test_retrieve_auto_keyword_and_explicit_hybrid_preflight():
    service,catalog,_=build_service();p=product();p.retrieval.vector=None
    p.capabilities-={Capability.VECTOR,Capability.HYBRID};catalog.put(p,expected_version=p.version)
    calls=[];original=service.keyword.search
    service.keyword.search=lambda **kw:(calls.append(kw) or original(**kw))
    service.retrieve(principal(),p.id,RetrieveRequest(query='adverse'))
    assert len(calls)==1
    with pytest.raises(CapabilityUnavailable):service.retrieve(principal(),p.id,RetrieveRequest(query='adverse',mode='hybrid'))
    assert len(calls)==1


def test_index_plan_skips_existing_hnsw_despite_build_parameter_difference():
    p=native_product();p.retrieval.postgres.cast_vector=False
    report={'indexes':[{'name':'existing','method':'hnsw','columns':['chunk_vector'],'expressions':[],
        'usable':True,'definition':'USING hnsw (chunk_vector vector_cosine_ops) WITH (m=16, ef_construction=64)'}]}
    plan=index_plan(p,report)
    assert not any('USING hnsw' in s for s in plan['statements'])
    assert plan['skipped'][0]['existing_index']=='existing'


def test_control_cache_is_request_scoped_and_invalidates_on_write(store):
    service,actor=runtime_service(store);queries=[]
    def capture(conn,cursor,statement,parameters,context,many):queries.append(statement)
    event.listen(store.engine,'before_cursor_execute',capture)
    with execution_context():
        a=store.get('clients','app');b=store.get('clients','app')
        assert a==b and len(queries)==1
        saved=service.control.clients.get('app');service.control.clients.put(saved.model_copy(update={'status':'disabled'}))
        assert store.get('clients','app')['payload']['status']=='disabled'
    event.remove(store.engine,'before_cursor_execute',capture)


def test_mcp_authenticated_initialize_list_and_call(store):
    app,service,actor=app_for(store)
    service.control.agents.put(AgentRegistration(id='agent',display_name='Agent',owner='team',service_principal=actor.subject,
        allowed_datasets={'facts'},allowed_capabilities={Capability.QUERY},mcp_enabled=True))
    manager=register_mcp(app,SimpleNamespace(service=service,catalog=service.catalog,control=service.control,store=store))
    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():yield
    app.router.lifespan_context=lifespan
    with TestClient(app) as c:
        headers={'Authorization':'Bearer agent','Accept':'application/json, text/event-stream'}
        init={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}
        assert c.post('/mcp',json=init).status_code==401
        assert c.post('/mcp',json=init,headers={**headers,'Authorization':'Bearer reader'}).status_code==403
        response=c.post('/mcp',json=init,headers=headers)
        assert response.status_code==200,response.text
        response=c.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/list'},headers=headers)
        names={t['name'] for t in response.json()['result']['tools']}
        assert names=={'describe_dataset','query_dataset'}
        response=c.post('/mcp',json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'query_dataset','arguments':{'dataset_id':'facts','request':{'limit':1}}}},headers=headers)
        assert response.status_code==200,response.text
        result=response.json()['result'];assert not result.get('isError'),result
        data=json.loads(result['content'][0]['text']);assert len(data['rows'])==1


def test_principal_bound_filters_are_admin_only_and_fail_closed():
    from enterprise_data_platform.models import AccessPolicy
    from enterprise_data_platform.policy import PolicyEngine
    from enterprise_data_platform.query_validation import validate_filter
    p=product();p.mandatory_filter={'field':'tenant_id','op':'eq','value':{'$principal':'tenant'}}
    p.validate_contract()
    policy=PolicyEngine([AccessPolicy(id='owner',effect='allow',operations={Capability.QUERY})])
    d=policy.evaluate(principal=principal(),product=p,operation=Capability.QUERY)
    assert d.allowed
    assert '$principal' not in json.dumps(d.mandatory_filter)
    assert not policy.evaluate(principal=principal().model_copy(update={'tenant':None}),product=p,operation=Capability.QUERY).allowed
    with pytest.raises(ValueError):validate_filter(p,p.mandatory_filter)
