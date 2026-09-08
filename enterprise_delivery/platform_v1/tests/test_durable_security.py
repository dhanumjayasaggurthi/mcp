from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import threading

import pytest
from sqlalchemy import create_engine, select

from enterprise_data_platform.catalog import CatalogConflict
from enterprise_data_platform.context import ExecutionContext
from enterprise_data_platform.control_models import ClientRegistration
from enterprise_data_platform.cursor import CursorCodec, CursorError, EncryptedCursorCodec
from enterprise_data_platform.durable import RelationalStore, SQLCatalog, SQLControlState, SQLPolicyEngine, audits, migrate
from enterprise_data_platform.governor import Limits, Overloaded, ResourceGovernor
from enterprise_data_platform.models import AccessPolicy, Capability, PolicyEffect, Principal, RetrieveRequest, StructuredQueryRequest
from enterprise_data_platform.query_validation import QueryValidationError, referenced_filter_fields
from enterprise_data_platform.services import AccessDenied
from test_platform_core import allow_policy, build_service, principal, product


@pytest.fixture
def store(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'control.db'), pool_size=8, max_overflow=0,
        connect_args={'timeout': 5})
    migrate(engine)
    yield RelationalStore(engine, production=False)
    engine.dispose()


def test_catalog_and_policy_survive_new_instances_and_conflict(store):
    a, b = SQLCatalog(store), SQLCatalog(RelationalStore(store.engine, production=False))
    p = product(); a.put(p)
    assert b.get(p.id) == p
    a.put(p.model_copy(update={'version': '2'}), expected_version=p.version)
    with pytest.raises(CatalogConflict):
        b.put(p.model_copy(update={'version': '3'}), expected_version=p.version)
    policies = SQLPolicyEngine(store); policies.put(allow_policy())
    other = SQLPolicyEngine(RelationalStore(store.engine, production=False))
    assert other.evaluate(principal=principal(), product=p, operation=Capability.QUERY).allowed
    denied = policies.list()[0].model_copy(update={'effect': PolicyEffect.DENY})
    policies.put(denied)
    assert not other.evaluate(principal=principal(), product=p, operation=Capability.QUERY).allowed
    with store.engine.connect() as conn:
        assert len(conn.execute(select(audits)).all()) == 4


def test_registry_revision_prevents_lost_update(store):
    registry = SQLControlState(store).clients
    saved = registry.put(ClientRegistration(id='app', display_name='App', owner='team'))
    registry.put(saved.model_copy(update={'display_name': 'New'}))
    with pytest.raises(CatalogConflict):
        registry.put(saved)
    with pytest.raises(CatalogConflict):
        registry.delete('app')


def test_tenant_isolation_does_not_depend_on_policy_flag():
    service, _, policies = build_service()
    policies.put(allow_policy().model_copy(update={'require_tenant_isolation': False}))
    response = service.query(principal(), product().id, StructuredQueryRequest(select=['id', 'tenant_id']))
    assert all(row['tenant_id'] == 'acme' for row in response.rows)
    with pytest.raises(AccessDenied):
        service.query(principal().model_copy(update={'tenant': None}), product().id, StructuredQueryRequest())


def test_exact_count_needs_explicit_capability_and_policy():
    service, _, _ = build_service()
    with pytest.raises(AccessDenied):
        service.query(principal(), product().id, StructuredQueryRequest(count_mode='exact'))


def test_cursor_principal_and_filter_scopes():
    service, _, _ = build_service()
    req = StructuredQueryRequest(select=['id'], limit=1)
    cursor = service.query(principal(), product().id, req).next_cursor
    with pytest.raises(CursorError, match='scope'):
        service.query(principal('other'), product().id, req.model_copy(update={'cursor': cursor}))
    with pytest.raises(CursorError, match='scope'):
        service.query(principal(), product().id, req.model_copy(update={'cursor': cursor, 'filter': {'field': 'id', 'op': 'eq', 'value': '2'}}))


def test_cursor_typed_positions_and_encryption():
    codec = EncryptedCursorCodec(b'c' * 32)
    position = {'time': datetime.now(timezone.utc), 'amount': Decimal('100.234')}
    token = codec.encode(dataset_id='d', dataset_version='1', position=position, sort=[])
    assert 'amount' not in token
    assert codec.decode(token, dataset_id='d', dataset_version='1')['position'] == position
    with pytest.raises(CursorError):
        codec.decode(token[:-10] + 'wrong', dataset_id='d', dataset_version='1')


def test_rag_poisoned_hydration_and_hidden_text_are_blocked():
    service, _, policies = build_service()
    for chunk in service.chunks.chunks_by_dataset[product().id].values():
        chunk['metadata']['tenant_id'] = 'evil'
    assert not service.retrieve(principal(), product().id, RetrieveRequest(query='adverse', mode='keyword')).results
    policies.put(allow_policy().model_copy(update={'denied_fields': {'body'}}))
    with pytest.raises(AccessDenied, match='text sources'):
        service.retrieve(principal(), product().id, RetrieveRequest(query='adverse'))


def test_filter_complexity_and_ambiguous_nodes_rejected():
    with pytest.raises(QueryValidationError):
        referenced_filter_fields({'and': [{'field': 'x', 'op': 'eq', 'value': 1}], 'or': []})
    expr = {'field': 'x', 'op': 'eq', 'value': 1}
    for _ in range(20):
        expr = {'not': expr}
    with pytest.raises(QueryValidationError, match='complexity'):
        referenced_filter_fields(expr)


def test_shared_governors_enforce_one_limit_and_release(store):
    limits = Limits(principal_concurrency=1)
    a, b = ResourceGovernor(store, limits), ResourceGovernor(store, limits)
    with a.admit(principal(), 'source', 'interactive', ExecutionContext()):
        with pytest.raises(Overloaded):
            with b.admit(principal(), 'source', 'interactive', ExecutionContext()):
                pytest.fail('over-admitted')
    with b.admit(principal(), 'source', 'interactive', ExecutionContext()):
        pass


def test_different_row_grants_cannot_cross_multiply_field_grants():
    service, _, policies = build_service()
    policies.put(allow_policy().model_copy(update={'allowed_fields': {'id', 'title'},
        'mandatory_filter': {'field': 'id', 'op': 'eq', 'value': '1'}}))
    policies.put(allow_policy().model_copy(update={'id': 'second', 'allowed_fields': {'id', 'body'},
        'mandatory_filter': {'field': 'id', 'op': 'eq', 'value': '2'}}))
    with pytest.raises(QueryValidationError):
        service.query(principal(), product().id, StructuredQueryRequest(select=['id', 'body']))
