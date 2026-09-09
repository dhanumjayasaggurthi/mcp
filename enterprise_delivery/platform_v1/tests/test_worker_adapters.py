import json
from pathlib import Path
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select, update

from enterprise_data_platform.backends import DeterministicHashEmbeddingProvider
from enterprise_data_platform.chunks import SQLChunkStore
from enterprise_data_platform.context import ExecutionContext, execution_context
from enterprise_data_platform.durable import RelationalStore, jobs, migrate, records
from enterprise_data_platform.durable_exports import DurableExportBackend, ExportWorker
from enterprise_data_platform.governor import Limits, Overloaded
from enterprise_data_platform.ingestion import IngestionBridge, IngestionEvent, IngestionWorker
from enterprise_data_platform.jobs import DurableQueue, LeaseLost
from enterprise_data_platform.models import Capability, ExportRequest
from enterprise_data_platform.rest_connector import pinned_url
from enterprise_data_platform.search import OpenSearchBackend, OpenSearchSink, OpenSearchTransport
from enterprise_data_platform.resilience import BackendUnavailable
from test_platform_core import allow_policy, build_service, principal, product
from test_durable_security import store


class Sink:
    fail = False
    def write_record(self, **kwargs):
        if self.fail:
            raise ConnectionError('sink unavailable')


class LocalObjects:
    def __init__(self, root):
        self.root = root
        self.keys = []
    def put_file(self, key, path):
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
        self.keys.append(key)
    def delete(self, key):
        (self.root / key).unlink(missing_ok=True)
    def signed_url(self, key, seconds):
        return 'https://download.example/' + key


def test_queue_restart_idempotency_capacity_fencing_and_cancellation(store):
    a = DurableQueue(store, capacity=1)
    submitted = a.enqueue('export', 'scope', {'x': 1}, idempotency_key='request-1')
    assert a.enqueue('export', 'scope', {'x': 1}, idempotency_key='request-1')['id'] == submitted['id']
    with pytest.raises(Overloaded):
        a.enqueue('export', 'scope', {'x': 2})
    b = DurableQueue(RelationalStore(store.engine, production=False))
    claimed = b.claim('export')
    assert claimed['id'] == submitted['id']
    b.cancel(claimed['id'], scope='scope')
    with pytest.raises(LeaseLost):
        b.complete(claimed, {'wrong': True})
    assert a.get(claimed['id'])['status'] == 'cancelled'
    assert a.enqueue('export', 'scope', {'x': 2})


def test_expired_job_gets_new_fencing_token(store):
    queue = DurableQueue(store)
    queue.enqueue('export', 'scope', {})
    old = queue.claim('export')
    with store.engine.begin() as conn:
        conn.execute(update(jobs).values(lease_until=0))
    new = queue.claim('export')
    assert old['token'] != new['token']
    with pytest.raises(LeaseLost):
        queue.complete(old, {})
    queue.complete(new, {})


def test_ingestion_partial_failure_duplicate_out_of_order_and_tombstone(store):
    _, catalog, _ = build_service()
    queue = DurableQueue(store); bridge = IngestionBridge(queue)
    chunks = SQLChunkStore(store); keyword, vector = Sink(), Sink()
    worker = IngestionWorker(queue=queue, catalog=catalog, chunk_store=chunks, keyword_sink=keyword,
        vector_sink=vector, embedder=DeterministicHashEmbeddingProvider())
    def event(seq, operation='upsert'):
        return IngestionEvent(dataset_id=product().id, dataset_version=product().version, index_version='idx-001',
            partition='p0', sequence=seq, event_id=f'e{seq}', operation=operation, record_id='record',
            source_version=str(seq), values={'id': 'record', 'tenant_id': 'acme', 'title': 'Title', 'body': 'clear text'})
    bridge.publish(event(2)); job = queue.claim('ingestion')
    vector.fail = True
    with pytest.raises(ConnectionError):
        worker.process(job)
    assert queue.get(job['id'])['status'] == 'running'
    with store.engine.connect() as conn:
        assert not conn.execute(select(records)).all()
    vector.fail = False; worker.process(job)
    with store.engine.connect() as conn:
        row = conn.execute(select(records)).mappings().one()
    assert chunks.get_chunks(product=product(), chunk_ids=row['chunk_ids'])
    assert bridge.publish(event(2))['status'] == 'succeeded'
    bridge.publish(event(1)); worker.process(queue.claim('ingestion'))
    with store.engine.connect() as conn:
        assert conn.execute(select(records.c.sequence)).scalar_one() == '2'
    bridge.publish(event(3, 'delete')); worker.process(queue.claim('ingestion'))
    assert not chunks.get_chunks(product=product(), chunk_ids=row['chunk_ids'])


@pytest.mark.parametrize('format,compression', [('parquet','zstd'), ('csv','gzip'), ('jsonl',None)])
def test_export_worker_streams_masked_governed_data_and_manifest(store, tmp_path, format, compression):
    service, catalog, policies = build_service()
    p = product(); catalog.put(p.model_copy(update={'capabilities': p.capabilities | {Capability.EXPORT}}))
    policies.put(allow_policy().model_copy(update={'id': 'export', 'operations': {Capability.EXPORT}}))
    objects = LocalObjects(tmp_path / 'objects'); queue = DurableQueue(store)
    backend = DurableExportBackend(queue, objects); service.exporter = backend
    job = service.export(principal(), p.id, ExportRequest(select=['id', 'secret'], format=format, compression=compression))
    worker = ExportWorker(queue=queue, service=service, object_storage=objects, limits=Limits(), part_rows=2)
    worker.process(queue.claim('export'))
    saved = queue.get(job.id)
    assert saved['status'] == 'succeeded'
    assert saved['result']['row_count'] == 3
    assert len(saved['result']['parts']) == 2
    first_path = objects.root / saved['result']['parts'][0]['key']
    if format == 'parquet':
        import pyarrow.parquet as pq
        rows = pq.read_table(first_path).to_pylist()
        assert rows[0]['secret'] == '***MASKED***'
    assert backend.download(principal(), job.id, service)['expires_in_seconds'] == 300
    policies.put(allow_policy().model_copy(update={'id': 'export-deny', 'effect': 'deny', 'operations': {Capability.EXPORT}}))
    with pytest.raises(PermissionError):
        backend.download(principal(), job.id, service)


def test_search_prefilter_and_partial_bulk_failures():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'hits': {'hits': []}, '_shards': {'failed': 0}})
    client = httpx.Client(base_url='https://search.example', transport=httpx.MockTransport(handler))
    transport = OpenSearchTransport('https://search.example', client=client)
    backend = OpenSearchBackend(transport, 'vector')
    backend.search(product=product(), filter_expr={'field': 'tenant_id', 'op': 'eq', 'value': 'acme'}, top_k=3, vector=[.1]*16)
    sent = requests[0]
    assert sent['track_total_hits'] is False
    assert 'acme' in json.dumps(sent['query']['knn']['vector']['filter'])
    assert 'acl.subjects' in json.dumps(sent)
    client.close()


def test_search_incomplete_shards_are_rejected():
    transport = OpenSearchTransport('https://search.example', client=httpx.Client(base_url='https://search.example',
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'_shards': {'failed': 1}}))))
    with pytest.raises(BackendUnavailable):
        transport.request('POST', '/index/_search', body={})


def test_failed_export_upload_cleans_partial_objects_and_never_completes(store, tmp_path):
    service,catalog,policies=build_service()
    p=product();catalog.put(p.model_copy(update={'capabilities':p.capabilities|{Capability.EXPORT}}))
    policies.put(allow_policy().model_copy(update={'id':'export','operations':{Capability.EXPORT}}))
    objects=LocalObjects(tmp_path/'objects');queue=DurableQueue(store)
    backend=DurableExportBackend(queue,objects);service.exporter=backend
    job=service.export(principal(),p.id,ExportRequest(select=['id'],format='jsonl',compression=None))
    original_put=objects.put_file
    def fail_after_upload(key,path):
        original_put(key,path)
        raise ConnectionError('object storage interrupted after accepting bytes')
    objects.put_file=fail_after_upload
    worker=ExportWorker(queue=queue,service=service,object_storage=objects,limits=Limits())
    with pytest.raises(ConnectionError):
        worker.process(queue.claim('export'))
    assert queue.get(job.id)['status']=='running'
    assert not list(objects.root.rglob('*.jsonl'))


def test_rest_ssrf_dns_rebinding_is_pinned_and_private_addresses_blocked(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='prohibited'):
        pinned_url('https://api.example', ['api.example'])
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443))])
    assert pinned_url('https://api.example/query', ['api.example']) == ('https://8.8.8.8/query', 'api.example')
    with pytest.raises(ValueError):
        pinned_url('https://api.example@evil.example', ['api.example'])
