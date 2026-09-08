"""Reproducible, bounded SQL baseline/new benchmark; never asserts scale from metadata."""
import argparse
import json
import math
import platform
import resource
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, create_engine
from enterprise_data_platform.catalog import InMemoryCatalog
from enterprise_data_platform.cursor import CursorCodec
from enterprise_data_platform.models import (AccessPolicy, Capability, DataProduct, FieldDefinition,
    Principal, SourceBinding, StructuredQueryRequest)
from enterprise_data_platform.policy import PolicyEngine
from enterprise_data_platform.services import PlatformService
from enterprise_data_platform.sqlalchemy_backend import SQLAlchemyStructuredBackend


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--rows', type=int, choices=[10_000, 1_000_000, 100_000_000, 1_000_000_000], default=1_000_000)
    p.add_argument('--database', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--requests', type=int, default=120)
    a = p.parse_args()
    engine = create_engine('sqlite:///' + a.database, pool_size=8, max_overflow=0, pool_timeout=2)
    md = MetaData()
    table = Table('bench', md, Column('id', Integer, primary_key=True), Column('tenant', String), Column('body', String))
    Index('bench_tenant_id', table.c.tenant, table.c.id)
    if not Path(a.database).exists():
        md.create_all(engine)
        for start in range(0, a.rows, 5000):
            with engine.begin() as c:
                c.execute(table.insert(), [dict(id=i, tenant=str(i % 10), body='synthetic record') for i in range(start, min(start+5000, a.rows))])
    product = DataProduct(id='bench', display_name='Benchmark', version='1', status='active',
        source=SourceBinding(connector='sqlite', environment='test', object_name='bench'),
        identity_fields=['id'], tenant_field='tenant', capabilities={Capability.QUERY},
        fields=[FieldDefinition(name='id', data_type='int', filterable=True, sortable=True),
                FieldDefinition(name='tenant', data_type='string', filterable=True),
                FieldDefinition(name='body', data_type='string')])
    catalog = InMemoryCatalog(); catalog.put(product)
    service = PlatformService(catalog=catalog, policies=PolicyEngine([AccessPolicy(id='bench', effect='allow',
        operations={Capability.QUERY}, require_tenant_isolation=True)]), cursor_codec=CursorCodec(b'b'*32),
        structured=SQLAlchemyStructuredBackend(lambda _: engine))
    principal = Principal(subject='benchmark', tenant='1')
    request = StructuredQueryRequest(select=['id', 'body'], limit=100)
    first = service.query(principal, 'bench', request)
    request = request.model_copy(update={'cursor': first.next_cursor})
    def run(_):
        t = time.perf_counter()
        result = service.query(principal, 'bench', request)
        assert len(result.rows) == 100
        return (time.perf_counter()-t)*1000
    report = dict(label=a.label, rows=a.rows, python=platform.python_version(), source='SQLite local indexed synthetic table',
        scope='SQL query + policy + second-page keyset; reference catalog/policy only; no distributed claims', measurements=[])
    for concurrency in [1, 4, 16]:
        cpu = time.process_time(); start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            timings = sorted(pool.map(run, range(a.requests)))
        duration = time.perf_counter()-start
        report['measurements'].append(dict(concurrency=concurrency, requests=a.requests, throughput_rps=a.requests/duration,
            **{f'p{n}_ms':timings[max(0, math.ceil(n/100*len(timings))-1)] for n in [50,95,99]},
            cpu_seconds=time.process_time()-cpu, max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            pool_limit=8, errors=0, timeouts=0))
    Path(a.output).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    engine.dispose()


if __name__ == '__main__':
    main()
