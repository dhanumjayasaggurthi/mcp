import pytest
from sqlalchemy import Column, Integer, MetaData, Table, Text
from sqlalchemy.dialects import postgresql

from enterprise_data_platform.models import (DataProduct, FieldDefinition, FieldPolicy, PostgresRetrievalProfile,
    ProductStatus, RetrievalProfile, SourceBinding, TextProfile, VectorProfile, Capability)
from enterprise_data_platform.onboarding import index_plan
from enterprise_data_platform.postgres_retrieval import PostgresRetrievalBackend, VectorType


def native_product():
    return DataProduct(id='rdh.clinical',display_name='Clinical chunks',version='1',status=ProductStatus.DRAFT,
        source=SourceBinding(connector='postgres',source_id='rdh-source',environment='prod',schema_name='rimdocs_extracts_core',object_name='doc_chunks_clinical'),
        identity_fields=['chunk_id'],fields=[FieldDefinition(name=n,data_type=t,filterable=f,sortable=n!='chunk_vector',
            selectable=n!='chunk_vector',default_policy=FieldPolicy.HIDDEN if n=='chunk_vector' else FieldPolicy.VISIBLE)
            for n,t,f in [('chunk_id','text',True),('doc_id','text',True),('chunk_index','int4',True),
                          ('chunk_text','text',True),('chunk_vector','vector',False),('updated_at','timestamptz',True)]],
        capabilities={Capability.QUERY,Capability.KEYWORD,Capability.VECTOR,Capability.RETRIEVE},
        retrieval=RetrievalProfile(backend='postgres',postgres=PostgresRetrievalProfile(cast_vector=True),
            text=TextProfile(source_fields=['chunk_text']),vector=VectorProfile(profile_id='registered-v1',
            embedding_model='registered-model',dimensions=8,index_version='1')))


def test_native_hnsw_sql_is_bounded_parameterized_and_index_compatible():
    p=native_product();p.validate_contract()
    table=Table('doc_chunks_clinical',MetaData(),Column('chunk_id',Text),Column('doc_id',Text),
        Column('chunk_text',Text),Column('chunk_vector',VectorType(8)),schema='rimdocs_extracts_core')
    adapter=PostgresRetrievalBackend(None,'vector')
    stmt,kind=adapter.statement(p,table,[table.c.chunk_id],[table.c.doc_id=="doc';DROP TABLE x;--"],None,[1]*8,10)
    sql=stmt.compile(dialect=postgresql.dialect())
    assert '<=>' in str(sql)
    assert 'LIMIT' in str(sql)
    assert 'DROP TABLE' not in str(sql)
    assert sql.params['query_embedding']=='[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]'
    assert kind=='pgvector_cosine'
    with pytest.raises(ValueError,match='zero'):
        adapter.statement(p,table,[table.c.chunk_id],[],None,[0]*8,10)


def test_native_keyword_uses_full_text_config_and_bound_web_query():
    p=native_product();p.validate_contract()
    table=Table('docs',MetaData(),Column('chunk_id',Text),Column('chunk_text',Text))
    stmt,kind=PostgresRetrievalBackend(None,'keyword').statement(p,table,[table.c.chunk_id],[],"' DROP TABLE; --",None,5)
    sql=stmt.compile(dialect=postgresql.dialect())
    assert 'websearch_to_tsquery' in str(sql)
    assert "'pg_catalog.english'::regconfig" in str(sql)
    assert 'DROP TABLE' not in str(sql)
    assert kind=='postgres_ts_rank_cd'


def test_index_plan_quotes_identifiers_and_matches_native_cast():
    p=native_product();p.source.object_name='odd"table'
    result=index_plan(p)
    assert result['executed'] is False
    assert all('DROP' not in s for s in result['statements'])
    assert '"odd""table"' in result['statements'][0]
    assert 'vector(8)' in result['statements'][2]
    assert 'CONCURRENTLY' in result['statements'][0]
    assert '"doc_id", "chunk_index", "chunk_id"' in result['statements'][1]
