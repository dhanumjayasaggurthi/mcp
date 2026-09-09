"""Bounded end-to-end load client. Credentials are read from EDP_BENCH_TOKEN."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import resource
import time
import uuid
from urllib.parse import quote, urlsplit

import httpx


def percentile(values, p):
    return sorted(values)[min(len(values)-1, int((len(values)-1)*p))] if values else None


async def run(args, concurrency):
    dataset = quote(args.dataset, safe='')
    prefix = '/v1/datasets/' + dataset
    headers = {'Authorization':'Bearer '+os.environ['EDP_BENCH_TOKEN']}
    status = Counter(); latencies=[]; accepted=0
    counter = iter(range(args.requests))
    async with httpx.AsyncClient(base_url=args.endpoint, headers=headers, follow_redirects=False,
        limits=httpx.Limits(max_connections=concurrency), timeout=35) as client:
        async def request(n):
            if args.scenario in {'structured','cursor','metadata'}:
                body={'limit':100,'count_mode':'none'}
                if args.scenario == 'metadata':
                    body['filter']=json.loads(args.filter)
                response=await client.post(prefix+'/query',json=body)
                if args.scenario == 'cursor' and response.is_success and response.json().get('next_cursor'):
                    response=await client.post(prefix+'/query',json={**body,'cursor':response.json()['next_cursor']})
                return response
            if args.scenario == 'export':
                response=await client.post(prefix+'/exports',json={'format':'parquet'},headers={'Idempotency-Key':str(uuid.uuid4())})
                if response.is_success:
                    return await terminal('/v1/exports/'+response.json()['id'])
                return response
            if args.scenario == 'indexing':
                body=json.loads(Path(args.event_template).read_text())
                identifier=f'benchmark-{args.run_id}-{concurrency}-{n}'
                body.update(record_id=identifier,event_id=identifier)
                response=await client.post('/v1/control/ingestion-events',json=body)
                if response.is_success:
                    return await terminal('/v1/control/jobs/'+response.json()['job_id'])
                return response
            body={'query':args.query,'top_k':10}
            route={'keyword':'search/keyword','vector':'search/vector','hybrid':'search/hybrid',
                'retrieve':'retrieve','hydration':'retrieve'}[args.scenario]
            if args.scenario == 'vector':
                body={'query_text':args.query,'top_k':10}
            if args.scenario == 'hydration':
                body['mode']='keyword'
            return await client.post(prefix+'/'+route,json=body)

        async def terminal(path):
            deadline=time.monotonic()+args.job_timeout
            while time.monotonic()<deadline:
                response=await client.get(path)
                if not response.is_success:
                    return response
                state=response.json()['status']
                if state in {'succeeded','failed','cancelled'}:
                    if state != 'succeeded':
                        raise RuntimeError('job_'+state)
                    return response
                await asyncio.sleep(.5)
            raise TimeoutError('job_timeout')

        async def worker():
            nonlocal accepted
            for n in counter:
                started=time.perf_counter()
                try:
                    response=await request(n)
                    status[str(response.status_code)]+=1
                    accepted+=int(response.is_success)
                except httpx.TimeoutException:
                    status['timeout']+=1
                except Exception as exc:
                    status[type(exc).__name__]+=1
                latencies.append((time.perf_counter()-started)*1000)
        started=time.perf_counter();cpu=time.process_time()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        seconds=time.perf_counter()-started
    return {'concurrency':concurrency,'requests':args.requests,'successful_operations':accepted,
        'attempted_operations_per_second':args.requests/seconds,'successful_operations_per_second':accepted/seconds,
        'p50_ms':percentile(latencies,.5),'p95_ms':percentile(latencies,.95),'p99_ms':percentile(latencies,.99),
        'client_cpu_seconds':time.process_time()-cpu,'client_max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'status_counts':dict(status),'source_connections':None,'queue_depth':None,
        'server_metrics':'Collect the server OTLP/source/queue metrics for this run; client measurements are not server metrics.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--endpoint',required=True);p.add_argument('--dataset',required=True)
    p.add_argument('--scenario',choices=['structured','cursor','metadata','keyword','vector','hybrid','retrieve','hydration','indexing','export'],required=True)
    p.add_argument('--query',default='synthetic document');p.add_argument('--filter',default='{}')
    p.add_argument('--event-template');p.add_argument('--allow-writes',action='store_true')
    p.add_argument('--requests',type=int,default=1000);p.add_argument('--concurrency',default='1,4,16,64')
    p.add_argument('--job-timeout',type=int,default=300);p.add_argument('--profile',choices=['1m','100m','1b'],default='1m')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    parsed=urlsplit(args.endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme not in {'http','https'}:
        p.error('endpoint must not contain credentials or query parameters')
    if not 1<=args.requests<=100000:
        p.error('requests must be 1..100000')
    levels=[int(x) for x in args.concurrency.split(',')]
    if not levels or any(not 1<=x<=256 for x in levels):
        p.error('concurrency must be 1..256')
    if args.scenario in {'indexing','export'} and not args.allow_writes:
        p.error('indexing/export benchmarks require --allow-writes on an isolated benchmark environment')
    if args.scenario=='indexing' and not args.event_template:
        p.error('indexing requires --event-template matching the provisioned dataset')
    args.run_id=uuid.uuid4().hex
    output={'run_id':args.run_id,'profile':args.profile,'profile_note':'Operator-declared source cardinality; this client does not verify row counts.',
        'scenario':args.scenario,'measurements':[asyncio.run(run(args,n)) for n in levels]}
    args.output.write_text(json.dumps(output,indent=2)+'\n')


if __name__=='__main__':
    main()
