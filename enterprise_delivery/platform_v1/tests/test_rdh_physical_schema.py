"""The uploaded RDH column/index shape, without fabricated governance columns."""
import os,uuid
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine,text,select
from enterprise_data_platform.connectors import SQLConnector,ConnectorCapabilities
from enterprise_data_platform.models import SortField
from enterprise_data_platform.onboarding import DraftDatasetRequest,draft_dataset,validate_binding
from enterprise_data_platform.models import DataProduct
from enterprise_data_platform.sqlalchemy_backend import SQLAlchemyStructuredBackend


def test_rdh_original_indexes_and_deep_cursor_cost():
    dsn=os.getenv('EDP_TEST_NATIVE_DSN')
    if not dsn:pytest.skip('EDP_TEST_NATIVE_DSN is required')
    engine=create_engine(dsn);schema='rdh_physical_'+uuid.uuid4().hex
    try:
        with engine.begin() as c:
            c.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector')
            c.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            trgm=c.execute(text("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_trgm'")).scalar_one()
            q=engine.dialect.identifier_preparer.quote_identifier
            c.exec_driver_sql('CREATE SCHEMA '+schema)
            c.exec_driver_sql(f'''CREATE TABLE {schema}.ingestion_log_clinical(
                doc_id text PRIMARY KEY,file_path text UNIQUE NOT NULL,file_name text NOT NULL,file_hash text NOT NULL,
                file_size bigint,last_modified timestamptz,ingested_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),
                status text NOT NULL DEFAULT 'PENDING',error_message text,retry_count int,chunk_count int,pages_total int)''')
            c.exec_driver_sql(f'''CREATE TABLE {schema}.doc_chunks_clinical(
                chunk_id text PRIMARY KEY,doc_id text NOT NULL REFERENCES {schema}.ingestion_log_clinical(doc_id) ON DELETE CASCADE,
                file_name text NOT NULL,file_path text NOT NULL,chunk_index int NOT NULL,chunk_total int,chunk_level text,
                section_title text,page_start int,page_end int,content_types text[],chunk_text text NOT NULL,
                chunk_vector vector(8),created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now())''')
            # The fixture chooses dimension 8; live source dimensions must still be inspected.
            c.exec_driver_sql(f"INSERT INTO {schema}.ingestion_log_clinical(doc_id,file_path,file_name,file_hash) VALUES ('doc','/fixture','fixture.pdf','fixture')")
            c.exec_driver_sql(f"INSERT INTO {schema}.doc_chunks_clinical(chunk_id,doc_id,file_name,file_path,chunk_index,chunk_text) SELECT 'chunk-'||lpad(n::text,6,'0'),'doc','fixture.pdf','/fixture',n,'clinical adverse event '||n FROM generate_series(1,50000) n")
            for field,suffix in [('doc_id','doc'),('file_name','filename'),('chunk_level','level')]:
                c.exec_driver_sql(f'CREATE INDEX epod_chunks_{suffix}_idx_clinical ON {schema}.doc_chunks_clinical ({field})')
            c.exec_driver_sql(f'CREATE INDEX epod_chunks_vector_idx_clinical ON {schema}.doc_chunks_clinical USING hnsw (chunk_vector vector_cosine_ops) WITH (m=16,ef_construction=64)')
            c.exec_driver_sql(f'CREATE INDEX idx_doc_chunks_clinical_trgm ON {schema}.doc_chunks_clinical USING gin(chunk_text {q(trgm)}.gin_trgm_ops)')
            c.exec_driver_sql(f'ANALYZE {schema}.doc_chunks_clinical')
        connector=SQLConnector(engine,ConnectorCapabilities(plan_inspection=True),max_scan_rows=100000)
        router=SimpleNamespace(resolve=lambda p:connector,registry=SimpleNamespace(get=lambda _:SimpleNamespace(id='rdh',kind='postgres')))
        p=DataProduct.model_validate(draft_dataset(router,DraftDatasetRequest(source_id='rdh',schema_name=schema,
            object_name='doc_chunks_clinical',dataset_id='rdh.clinical',display_name='Clinical',template='postgres_chunks'))['dataset'])
        assert not {'tenant_id','approval_status','acl_groups'} & set(p.field_map())
        assert not validate_binding(router,p)['valid'] # Trigram is not full-text.
        p.retrieval.postgres.keyword_mode='contains'
        assert validate_binding(router,p)['valid']
        table=connector.backend._table(p,engine)
        where=SQLAlchemyStructuredBackend._keyset_after(table,[SortField(field='chunk_id')],{'chunk_id':'chunk-049000'})
        stmt=select(table.c.chunk_id).where(where).order_by(table.c.chunk_id).limit(101)
        sql=stmt.compile(dialect=engine.dialect,compile_kwargs={'literal_binds':True})
        with engine.connect() as c:
            plan=c.exec_driver_sql('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+str(sql)).scalar_one()[0]['Plan']
        def scans(node):
            yield node
            for child in node.get('Plans',[]):yield from scans(child)
        indexed=[n for n in scans(plan) if n.get('Index Cond')]
        assert indexed and all(n.get('Rows Removed by Filter',0)==0 for n in indexed)
        assert sum(n.get('Shared Hit Blocks',0)+n.get('Shared Read Blocks',0) for n in indexed)<200
    finally:
        with engine.begin() as c:c.exec_driver_sql('DROP SCHEMA IF EXISTS '+schema+' CASCADE')
        engine.dispose()
