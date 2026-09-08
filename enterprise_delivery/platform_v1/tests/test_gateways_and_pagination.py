from datetime import datetime, timezone
import time
import types
import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st
from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlalchemy.exc import TimeoutError as PoolTimeout

from enterprise_data_platform.api import create_app
from enterprise_data_platform.catalog import InMemoryCatalog
from enterprise_data_platform.compatibility import AliasRegistration, register_compatibility
from enterprise_data_platform.control_state import ControlState
from enterprise_data_platform.cursor import CursorCodec
from enterprise_data_platform.durable import SQLRegistry, objects
from enterprise_data_platform.durable_promotion import SQLIndexPromotionController
from enterprise_data_platform.identity import JWTPrincipalResolver, MountedSecretProvider
from enterprise_data_platform.models import AccessPolicy, Capability, DataProduct, FieldDefinition, Principal, SortField, SourceBinding, StructuredQueryRequest
from enterprise_data_platform.policy import PolicyEngine
from enterprise_data_platform.services import PlatformService
from enterprise_data_platform.sql_gateway import SQLGateway, UnsupportedSQL, translate
from enterprise_data_platform.sqlalchemy_backend import SQLAlchemyStructuredBackend
from enterprise_data_platform.control_models import IndexDeployment
from test_platform_core import build_service, principal, product
from test_durable_security import store


def test_jwt_verifies_issuer_audience_and_ignores_identity_headers():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = JWTPrincipalResolver(issuer='https://issuer.example', audience='fabric', jwks_url='https://issuer.example/jwks',
        jwks_client=types.SimpleNamespace(get_signing_key_from_jwt=lambda _: types.SimpleNamespace(key=key.public_key())))
    claims = {'sub':'real', 'client_id':'app', 'tenant':'tenant', 'groups':['readers'], 'scope':'edp:query',
        'iss':'https://issuer.example','aud':'fabric','iat':int(time.time()),'exp':int(time.time())+60}
    def request(payload):
        token = jwt.encode(payload, key, algorithm='RS256')
        return types.SimpleNamespace(headers={'authorization':'Bearer '+token, 'x-subject':'attacker', 'x-tenant':'evil'})
    result = resolver(request(claims))
    assert result.subject == 'real' and result.tenant == 'tenant'
    for wrong in [{'aud':'other'}, {'iss':'https://evil.example'}, {'exp':0}]:
        with pytest.raises(HTTPException) as error:
            resolver(request({**claims, **wrong}))
        assert error.value.status_code == 401


def test_secret_references_cannot_escape_mount(tmp_path):
    (tmp_path/'dsn').write_text('value')
    provider = MountedSecretProvider(tmp_path)
    assert provider.resolve('file://dsn') == 'value'
    with pytest.raises(ValueError):
        provider.resolve('file://../credentials')


@pytest.mark.parametrize('sql', ["SELECT * FROM x; DROP TABLE y", 'DELETE FROM x',
    'SELECT * FROM x JOIN y ON x.id=y.id', 'WITH q AS (SELECT * FROM x) SELECT * FROM q',
    'SELECT * FROM physical.secret', 'SELECT * FROM x OFFSET 1000000', 'SELECT pg_sleep(1) FROM x',
    'SELECT * FROM x WHERE id IN (SELECT id FROM y)', 'SELECT * FROM x FOR UPDATE',
    'SELECT * FROM x UNION SELECT * FROM y'])
def test_sql_gateway_rejects_unsafe_or_unsupported_constructs(sql):
    with pytest.raises((UnsupportedSQL, ValueError)):
        translate(sql)


def test_sql_and_legacy_alias_routes_share_service_governance(store):
    service, catalog, policies = build_service()
    aliases = SQLRegistry(store, 'aliases', AliasRegistration)
    aliases.put(AliasRegistration(id='consumer.docs', api_id='consumer', alias='docs', dataset_id=product().id))
    gateway = SQLGateway(service, aliases)
    response = gateway.execute(principal(), 'consumer', "SELECT id, title FROM docs WHERE tenant_id = 'acme' ORDER BY id LIMIT 2")
    assert [row['id'] for row in response.rows] == ['1','2']
    app = create_app(service=service, catalog=catalog, policies=policies, control_state=ControlState(),
        principal_resolver=lambda _: principal(), control_admin_check=lambda _: False)
    register_compatibility(app, service, aliases, app.state.principal_dependency)
    client = TestClient(app)
    first = client.get('/data-api/consumer/docs/rows?limit=2')
    assert first.status_code == 200, first.text
    assert first.json()['row_count'] == 2
    assert first.headers['deprecation'] == 'true'
    second = client.get('/data-api/consumer/docs/rows', params={'limit':2, 'cursor':first.json()['next_cursor']})
    assert [row['id'] for row in second.json()['rows']] == ['3']
    assert client.get('/data-api/consumer/docs/rows?offset=1000000').status_code == 422
    assert client.get('/data-api/consumer/docs/count').status_code == 403


@settings(max_examples=35, deadline=None)
@given(st.lists(st.one_of(st.none(), st.integers(-5,5)), min_size=1, max_size=35), st.sampled_from(['asc','desc']))
def test_compound_null_keyset_has_no_skips_or_duplicates(values, direction):
    engine = create_engine('sqlite:///:memory:')
    md = MetaData(); table = Table('numbers',md,Column('id',Integer,primary_key=True),Column('value',Integer))
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert(), [{'id':i,'value':v} for i,v in enumerate(values)])
    product = DataProduct(id='numbers', display_name='Numbers', version='1', status='active',
        source=SourceBinding(connector='sqlite',environment='test',object_name='numbers'),
        identity_fields=['id'], fields=[FieldDefinition(name='id',data_type='int',sortable=True),
            FieldDefinition(name='value',data_type='int',sortable=True)], capabilities={Capability.QUERY})
    catalog = InMemoryCatalog(); catalog.put(product)
    service = PlatformService(catalog=catalog, policies=PolicyEngine([AccessPolicy(id='allow',effect='allow')]),
        cursor_codec=CursorCodec(b'n'*32), structured=SQLAlchemyStructuredBackend(lambda _:engine))
    request = StructuredQueryRequest(select=['id','value'],order_by=[SortField(field='value',direction=direction)],limit=3)
    found = []
    while True:
        page = service.query(Principal(subject='tester'),product.id,request)
        found.extend(row['id'] for row in page.rows)
        if not page.next_cursor:
            break
        request.cursor = page.next_cursor
    expected = sorted(range(len(values)),key=lambda i:(values[i] is None, (values[i] or 0) * (1 if direction=='asc' else -1),i))
    assert found == expected
    engine.dispose()


def test_pool_exhaustion_is_bounded(tmp_path):
    engine = create_engine('sqlite:///'+str(tmp_path/'source.db'), pool_size=1, max_overflow=0, pool_timeout=.05)
    with engine.connect():
        start = time.monotonic()
        with pytest.raises(PoolTimeout):
            engine.connect()
        assert time.monotonic()-start < .5
    engine.dispose()


def test_promotion_atomically_switches_one_shared_route(store):
    registry = SQLRegistry(store,'indexes',IndexDeployment)
    for kind in ['keyword','vector']:
        registry.put(IndexDeployment(id='docs-'+kind,dataset_id='docs',index_type=kind,active_version='v1',candidate_version='v2',state='building'))
    controller = SQLIndexPromotionController(store)
    evidence = dict(evaluated_queries=100,recall_at_k=.99,precision_at_k=.99,citation_coverage=1,p95_latency_ms=20,error_rate=0)
    controller.transition('docs-vector','validate',evidence)
    controller.transition('docs-vector','canary',{'percent':100})
    assert store.get('routing','docs')['payload']['candidate_version'] == 'v2'
    controller.transition('docs-vector','promote')
    assert store.get('routing','docs')['payload']['active_version'] == 'v2'
    assert {d.active_version for d in registry.list()} == {'v2'}
