"""Seed isolated synthetic tables in bounded transactions, never application memory."""
import argparse
import os
from sqlalchemy import create_engine, text


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rows',type=int,choices=[1000000,100000000,1000000000],required=True)
    p.add_argument('--start',type=int,default=1,help='Resume after the last committed batch')
    args=p.parse_args()
    engine=create_engine(os.environ['EDP_BENCH_POSTGRES_DSN'],pool_size=1,max_overflow=0)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE SCHEMA IF NOT EXISTS edp_benchmark')
            conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS edp_benchmark.documents (id bigint PRIMARY KEY, tenant_id text NOT NULL, title text, body text, updated_at timestamptz NOT NULL)')
            conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS edp_benchmark_tenant_id ON edp_benchmark.documents (tenant_id,id)')
        for start in range(args.start,args.rows+1,100000):
            end=min(args.rows,start+99999)
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO edp_benchmark.documents SELECT i, 'tenant-'||(i%100), 'Synthetic '||i, repeat('synthetic searchable document ',8), TIMESTAMPTZ '2026-01-01' + ((i%365)::text||' days')::interval FROM generate_series(:start,:end) i ON CONFLICT (id) DO NOTHING"),{'start':start,'end':end})
            print('committed_through='+str(end),flush=True)
        with engine.begin() as conn:
            conn.exec_driver_sql('ANALYZE edp_benchmark.documents')
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
