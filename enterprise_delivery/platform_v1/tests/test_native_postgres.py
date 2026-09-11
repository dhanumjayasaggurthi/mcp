"""Source-realistic PostgreSQL/pgvector tests (no production data required)."""
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from enterprise_data_platform.api import create_app
from enterprise_data_platform.catalog import InMemoryCatalog
from enterprise_data_platform.connectors import SQLConnector, ConnectorCapabilities
from enterprise_data_platform.control_state import ControlState
from enterprise_data_platform.context import current_actor
from enterprise_data_platform.cursor import CursorCodec
from enterprise_data_platform.models import (AccessPolicy, Capability, FieldDefinition, PolicyEffect,
    PostgresRetrievalProfile, Principal, ProductStatus, SearchRequest, SortField, StructuredQueryRequest,
    VectorProfile, VectorSearchRequest)
from enterprise_data_platform.onboarding import (DraftDatasetRequest, InspectRequest, draft_dataset,
    index_plan, inspect_object, register_onboarding, validate_binding)
from enterprise_data_platform.policy import PolicyEngine
from enterprise_data_platform.postgres_retrieval import PostgresRetrievalBackend
from enterprise_data_platform.services import PlatformService


@pytest.fixture(scope='module')
def native_env():
    dsn = os.getenv('EDP_TEST_NATIVE_DSN')
    if not dsn: pytest.skip('EDP_TEST_NATIVE_DSN is not set')
    engine = create_engine(dsn, pool_size=4, max_overflow=0)
    schema = 'rdh_native_' + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector')
        version = conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()
        conn.exec_driver_sql('CREATE SCHEMA ' + schema)
        conn.exec_driver_sql(f'''CREATE TABLE {schema}.doc_chunks_clinical (
            chunk_id text PRIMARY KEY, doc_id text NOT NULL, file_name text NOT NULL,
            file_path text NOT NULL, chunk_index integer NOT NULL, chunk_total integer,
            chunk_level text, section_title text, page_start integer, page_end integer,
            content_types text[], chunk_text text NOT NULL, chunk_vector public.vector(8),
            created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
            attributes jsonb, approval_status text NOT NULL, tenant_id text NOT NULL,
            acl_groups text[]
        )''')
        # Extra approval/tenant/ACL fields model a governed source view. The
        # user's raw sample tables do not imply these business fields exist.
        conn.exec_driver_sql(f'''INSERT INTO {schema}.doc_chunks_clinical
            (chunk_id,doc_id,file_name,file_path,chunk_index,chunk_total,chunk_level,section_title,
             page_start,page_end,content_types,chunk_text,chunk_vector,attributes,approval_status,tenant_id,acl_groups)
            SELECT 'chunk-'||n, 'doc-'||((n-1)/5), 'protocol.pdf', '/approved/protocol.pdf', mod(n-1,5), 5,
              'section', 'Safety', mod(n-1,10)+1, mod(n-1,10)+2, ARRAY['text','table'],
              CASE WHEN n <= 20 THEN 'clinical adverse event protocol' ELSE 'manufacturing procedure' END,
              ('['||(mod(n,20)+1)::text||',1,0,0,0,0,0,0]')::vector(8),
              '{{"site":{{"country":"US"}},"flag":true}}'::jsonb,
              CASE WHEN n=2 THEN 'DRAFT' ELSE 'APPROVED' END,
              CASE WHEN n=3 THEN 'other' ELSE 'acme' END,
              CASE WHEN n=4 THEN ARRAY['restricted'] ELSE ARRAY['readers'] END
            FROM generate_series(1,10000) AS n''')
    connector = SQLConnector(engine, ConnectorCapabilities(aggregation=True, plan_inspection=True), max_scan_rows=20000)
    registration = SimpleNamespace(id='rdh-postgres', kind='postgres')
    router = SimpleNamespace(resolve=lambda p: connector, registry=SimpleNamespace(get=lambda name: registration),
                             query=connector.query, aggregate=connector.aggregate)
    iterative = tuple(int(v) for v in version.split('.')[:2]) >= (0, 8)
    request = DraftDatasetRequest(source_id='rdh-postgres', schema_name=schema, object_name='doc_chunks_clinical',
        dataset_id='rdh-clinical', display_name='Clinical chunks', template='postgres_chunks',
        tenant_field='tenant_id', mandatory_filter={'field':'approval_status','op':'eq','value':'APPROVED'},
        postgres=PostgresRetrievalProfile(acl_groups_field='acl_groups', iterative_scan=iterative,
            citation_fields={'title':'file_name','page_start':'page_start','section':'section_title'}),
        vector_profile=VectorProfile(profile_id='fixture-v1', embedding_model='fixture-only', dimensions=8, index_version='source-v1'))
    from enterprise_data_platform.models import DataProduct
    p = DataProduct.model_validate(draft_dataset(router, request)['dataset'])
    # Index DDL is produced by the same operator-facing plan used in production.
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        for statement in index_plan(p)['statements']: conn.exec_driver_sql(statement)
    try:
        yield engine, router, p, request
    finally:
        with engine.begin() as conn: conn.exec_driver_sql('DROP SCHEMA ' + schema + ' CASCADE')
        engine.dispose()


def service_for(router, p):
    p = p.model_copy(deep=True); p.status = ProductStatus.ACTIVE
    catalog = InMemoryCatalog(); catalog.put(p)
    policies = PolicyEngine([AccessPolicy(id='read', effect=PolicyEffect.ALLOW,
        dataset_patterns=[p.id], operations=p.capabilities, groups={'readers'})])
    service = PlatformService(catalog=catalog, policies=policies, cursor_codec=CursorCodec(b'k'*32),
        structured=router, keyword=PostgresRetrievalBackend(router,'keyword'),
        vector=PostgresRetrievalBackend(router,'vector'))
    principal = Principal(subject='reader', groups={'readers'}, tenant='acme', client_id='regassist')
    return service, catalog, policies, principal


def test_native_onboarding_preserves_real_schema_and_hides_embeddings(native_env):
    engine, router, p, request = native_env
    assert p.status == ProductStatus.DRAFT
    assert p.field_map()['content_types'].data_type == 'text[]'
    assert p.field_map()['attributes'].data_type == 'jsonb'
    assert not p.field_map()['chunk_vector'].selectable
    report = validate_binding(router, p)
    assert report['valid'], report['issues']
    invalid = p.model_copy(deep=True); invalid.retrieval.vector.dimensions = 16
    assert 'source embedding dimension does not match profile' in validate_binding(router, invalid)['issues']
    wrong_metric=p.model_copy(deep=True); wrong_metric.retrieval.vector.distance='l2'
    assert 'vector search requires a matching HNSW index' in validate_binding(router,wrong_metric)['issues']
    assert not index_plan(p)['executed']


def test_native_keyword_vector_filters_and_citations(native_env):
    engine, router, p, request = native_env
    service, catalog, policies, principal = service_for(router,p)
    token=current_actor.set(principal.model_dump())
    filters = {'and':[{'field':'doc_id','op':'eq','value':'doc-0'},
        {'field':'page_start','op':'between','value':[1,8]},
        {'field':'content_types','op':'array_overlaps','value':['table']},
        {'field':'attributes','path':['site','country'],'op':'eq','value':'US'}]}
    try:
        k=service.keyword_search(principal,p.id,SearchRequest(query='"adverse event"',filter=filters,return_text=True,top_k=10))
        v=service.vector_search(principal,p.id,VectorSearchRequest(vector=[1,1,0,0,0,0,0,0],
            vector_profile='fixture-v1',filter=filters,return_text=True,top_k=10))
    finally: current_actor.reset(token)
    for result, label in [(k,'keyword'),(v,'vector')]:
        assert {h.chunk_id for h in result.results} == {'chunk-1','chunk-5'}
        assert all(h.ranks[label] > 0 and label in h.scores for h in result.results)
        assert all(h.text and h.source['citations']['title']=='protocol.pdf' for h in result.results)
        assert all('chunk_vector' not in h.metadata for h in result.results)
        assert 'canonical' not in result.model_dump_json()
        assert result.index_version == 'source-v1'
    assert k.score_kind == 'postgres_ts_rank_cd'
    assert v.score_kind == 'pgvector_cosine'


def test_native_exact_lookup_and_multiple_fields_via_http(native_env):
    engine,router,p,request=native_env
    service,catalog,policies,principal=service_for(router,p)
    app=create_app(service=service,catalog=catalog,policies=policies,control_state=ControlState(),
        principal_resolver=lambda _:principal,control_admin_check=lambda _:True)
    runtime=SimpleNamespace(router=router)
    register_onboarding(app,runtime)
    client=TestClient(app)
    result=client.post('/v1/control/onboarding/preview',json=request.model_dump(mode='json'))
    assert result.status_code==200,result.text
    result=client.get('/v1/datasets/'+p.id+'/filters')
    assert result.status_code==200
    assert 'array_overlaps' in next(f for f in result.json()['fields'] if f['field']=='content_types')['operators']
    body={'ids':['doc-0'],'select':['chunk_id','doc_id','chunk_text','page_start'],
          'filter':{'field':'page_start','op':'gte','value':1},'limit':1,
          'order_by':[{'field':'chunk_index','direction':'asc'}]}
    response=client.post('/v1/datasets/'+p.id+'/records/lookup',json=body)
    assert response.status_code==200,response.text
    first=response.json(); assert first['rows'][0]['chunk_id']=='chunk-1'
    assert first['next_cursor']
    # Structured rows enforce dataset policy and tenant; add source ACL
    # predicates below in the shared service to protect direct-ID paths too.
    body['cursor']=first['next_cursor']
    response=client.post('/v1/datasets/'+p.id+'/records/lookup',json=body)
    assert response.status_code==200,response.text
    assert response.json()['rows'][0]['chunk_id']=='chunk-5'
    body['filter']={'field':'page_start','op':'gte','value':2}
    response=client.post('/v1/datasets/'+p.id+'/records/lookup',json=body)
    assert response.status_code==400


def test_native_source_json_contains_and_parameterized_injection(native_env):
    engine,router,p,_=native_env
    service,_,_,principal=service_for(router,p)
    for predicate in [
        {'field':'attributes','op':'json_contains','value':{'site':{'country':'US'}}},
        {'field':'attributes','path':['flag'],'op':'eq','value':True},
        {'field':'content_types','op':'array_contains_all','value':['text','table']},
        {'field':'file_name','op':'ends_with','value':'.pdf'}]:
        result=service.query(principal,p.id,StructuredQueryRequest(filter=predicate,limit=2))
        assert result.rows
    result=service.query(principal,p.id,StructuredQueryRequest(
        filter={'field':'doc_id','op':'eq','value':"x'; DROP TABLE doc_chunks_clinical; --"}))
    assert result.rows==[]
    assert inspect_object(router,'rdh-postgres',InspectRequest(schema_name=p.source.schema_name,object_name=p.source.object_name))['primary_key']==['chunk_id']


def test_source_delete_is_immediately_absent_without_replication(native_env):
    engine,router,p,_=native_env
    service,_,_,principal=service_for(router,p)
    with engine.begin() as conn:
        conn.execute(text(f'DELETE FROM {p.source.schema_name}.doc_chunks_clinical WHERE chunk_id=:id'),{'id':'chunk-10'})
    result=service.keyword_search(principal,p.id,SearchRequest(query='clinical',filter={'field':'chunk_id','op':'eq','value':'chunk-10'},return_text=True))
    assert result.results==[]


def test_native_planner_uses_fulltext_and_ann_indexes(native_env):
    engine, router, p, _ = native_env
    connector = router.resolve(p)
    table = connector.backend._table(p, engine)
    for kind in ['keyword', 'vector']:
        adapter = PostgresRetrievalBackend(router, kind)
        statement, _ = adapter.statement(p, table, [table.c.chunk_id], [], 'clinical', [1,1,0,0,0,0,0,0], 10)
        compiled = statement.compile(dialect=engine.dialect, compile_kwargs={'render_postcompile': True})
        params = {key: compiled._bind_processors[key](value) if key in compiled._bind_processors else value
                  for key, value in compiled.params.items()}
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout='10s'"))
            plan = conn.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+str(compiled), params).scalar_one()[0]
        def indexes(node):
            return [node.get('Index Name', '')] + [name for child in node.get('Plans', []) for name in indexes(child)]
        assert any(name.endswith('_fts' if kind == 'keyword' else '_hnsw') for name in indexes(plan['Plan'])), plan
        assert plan['Plan']['Actual Rows'] <= 10
        print({'retrieval':kind,'fixture_rows':10000,'execution_ms':plan['Execution Time']})


def test_native_heterogeneous_json_values_do_not_break_typed_filters(native_env):
    engine,router,p,_=native_env
    service,_,_,principal=service_for(router,p)
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE {p.source.schema_name}.doc_chunks_clinical SET attributes=CAST(:value AS jsonb) WHERE chunk_id='chunk-100'"),
                     {'value':'{"flag":"not a boolean","amount":"not a number"}'})
    for key,value in [('flag',True),('amount',42)]:
        result=service.query(principal,p.id,StructuredQueryRequest(filter={'and':[
            {'field':'chunk_id','op':'eq','value':'chunk-100'},
            {'field':'attributes','path':[key],'op':'eq','value':value}]}))
        assert result.rows==[]
