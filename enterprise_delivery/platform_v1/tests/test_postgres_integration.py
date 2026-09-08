"""Real PostgreSQL contracts. Set EDP_TEST_POSTGRES_DSN; CI provides a service."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import DBAPIError

from enterprise_data_platform.catalog import CatalogConflict
from enterprise_data_platform.context import ExecutionContext
from enterprise_data_platform.durable import RelationalStore, SQLCatalog, audits, chunks, migrate
from enterprise_data_platform.governor import Limits, Overloaded, ResourceGovernor
from enterprise_data_platform.jobs import DurableQueue
from enterprise_data_platform.models import CountMode, SortField, StructuredQueryRequest
from enterprise_data_platform.sqlalchemy_backend import SQLAlchemyStructuredBackend
from test_platform_core import principal, product
from test_production_boundaries import runtime_service
from test_worker_adapters import test_ingestion_partial_failure_duplicate_out_of_order_and_tombstone as exercise_ingestion

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_store():
    dsn = os.getenv('EDP_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('EDP_TEST_POSTGRES_DSN is not set')
    admin = create_engine(dsn, pool_size=1, max_overflow=0)
    schema = 'edp_test_' + uuid.uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql('CREATE SCHEMA ' + schema)
    engine = create_engine(dsn, pool_size=8, max_overflow=0, pool_timeout=2,
        connect_args={'options': '-c search_path=' + schema + ',public -c statement_timeout=5000 -c lock_timeout=2000'})
    try:
        migrate(engine); migrate(engine)
        yield RelationalStore(engine)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql('DROP SCHEMA ' + schema + ' CASCADE')
        admin.dispose()


def test_postgres_durable_revisions_and_append_only_audit(pg_store):
    a,b=SQLCatalog(pg_store),SQLCatalog(RelationalStore(pg_store.engine))
    p=product();a.put(p)
    a.put(p.model_copy(update={'version':'2'}),expected_version=p.version)
    with pytest.raises(CatalogConflict):
        b.put(p.model_copy(update={'version':'3'}),expected_version=p.version)
    with pytest.raises(DBAPIError, match='append-only'):
        with pg_store.engine.begin() as conn:
            conn.execute(update(audits).values(action='tamper'))
    with pg_store.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM pg_inherits WHERE inhparent=CAST('edp_chunks' AS regclass)")) == 32


def test_postgres_skip_locked_claims_are_unique(pg_store):
    queue=DurableQueue(pg_store)
    for n in range(24):
        queue.enqueue('export','scope',{'n':n})
    def drain(_):
        worker=DurableQueue(RelationalStore(pg_store.engine));seen=[]
        while job:=worker.claim('export'):
            seen.append(job['id']);worker.complete(job,{'done':True})
        return seen
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=[item for batch in pool.map(drain,range(4)) for item in batch]
    assert len(ids) == len(set(ids)) == 24


def test_postgres_shared_admission_never_exceeds_limit(pg_store):
    barrier=threading.Barrier(5);release=threading.Event()
    def attempt(_):
        governor=ResourceGovernor(RelationalStore(pg_store.engine),Limits(principal_concurrency=2))
        try:
            with governor.admit(principal(),'source','interactive',ExecutionContext()):
                barrier.wait(timeout=10);release.wait(timeout=10)
                return True
        except Overloaded:
            barrier.wait(timeout=10)
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(attempt,n) for n in range(4)]
        barrier.wait(timeout=10);release.set()
        assert sum(f.result() for f in futures) == 2


def test_postgres_cdc_partial_sink_failure_is_atomic(pg_store):
    exercise_ingestion(pg_store)


def test_postgres_governed_queries_estimates_and_scan_rejection(pg_store):
    service,actor=runtime_service(pg_store)
    with pg_store.engine.begin() as conn:
        conn.exec_driver_sql('ANALYZE facts')
    response=service.query(actor,'facts',StructuredQueryRequest(limit=1,count_mode=CountMode.ESTIMATE))
    assert response.count_is_estimate and response.count is not None
    second=service.query(actor,'facts',StructuredQueryRequest(limit=1,count_mode=CountMode.ESTIMATE,cursor=response.next_cursor))
    assert response.rows[0]['id'] != second.rows[0]['id']
    backend=SQLAlchemyStructuredBackend(lambda _:pg_store.engine,max_scan_rows=1)
    with pytest.raises(ValueError,match='scan exceeds'):
        backend.query(product=service.catalog.get('facts'),fields=['id'],filter_expr=None,
            order_by=[SortField(field='id')],limit=1,position=None,count_mode=CountMode.NONE)
