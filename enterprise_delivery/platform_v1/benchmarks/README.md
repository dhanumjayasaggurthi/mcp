# Reproducible performance harnesses

`structured.py` isolates the core governed SQL/keyset path on a real synthetic SQLite table. It supports 10K, 1M, 100M and 1B row creation, but only **1M was actually executed** for the committed baseline/new comparison. Seeding uses bounded batches; it does not allocate all rows in memory. Existing databases must match the declared row count.

`runs/baseline-{1,2,3}.json` and `runs/new-{1,2,3}.json` are paired measurements. `comparison.json` is their median summary; the root `PERFORMANCE_RESULTS.md` explains scope and limitations. The top-level `baseline.json` and `new.json` are earlier exploratory runs and are not mixed into the paired summary.

## Provision a PostgreSQL profile

Use an isolated benchmark database and supply `EDP_BENCH_POSTGRES_DSN` through the environment. The script creates only `edp_benchmark.documents` and its index; it never drops an existing schema. A 100M/1B profile may be expensive and requires operator-provisioned storage/compute.

```bash
python benchmarks/seed_postgres.py --rows 1000000
# Resume after a previously committed batch:
python benchmarks/seed_postgres.py --rows 1000000 --start 500001
```

Register the table as a Data Product with identity `id` and tenant field `tenant_id`. Use an approved source budget and explicit test policies/clients. For search, configure text/vector profiles, provision candidate indexes and publish snapshot/CDC pages through the normal indexing path. Record actual source/chunk/index counts and the snapshot watermark in the benchmark manifest; do not label a small index “1B” by changing only a command-line profile.

## Exercise the deployed API

Set `EDP_BENCH_TOKEN` in the environment. Tokens are sent only in headers and omitted from output. Configure the endpoint, dataset and scenario:

```bash
python benchmarks/http_load.py --endpoint https://fabric.example \
  --dataset benchmark-documents --scenario cursor --profile 1m \
  --requests 10000 --concurrency 1,4,16,64 --output cursor-load.json
```

Scenarios: `structured`, `cursor`, `metadata`, `keyword`, `vector`, `hybrid`, `retrieve`, `hydration`, `indexing`, `export`. Metadata uses `--filter` with the standard filter AST. Hydration measures keyword retrieval plus canonical hydration, not an ungoverned chunk endpoint. Vector mode includes query embedding latency.

Indexing/export scenarios require `--allow-writes`. Indexing additionally needs `--event-template` with dataset/schema/index version, partition, sequence, source version, values and ACL matching the registered product. IDs are unique per run. Use admin credentials for indexing and appropriate export grants for export. Both scenarios poll durable job completion and report failed/cancelled/timed-out jobs as errors, rather than calling queue admission completion. Clean benchmark indexes/data/objects through the environment's controlled retention process.

The client limits workers/connections to the chosen concurrency and caps request count at 100,000. It reports attempted and successful operation throughput, p50/p95/p99, status counts and **client** CPU/RSS. Export/indexing times include queue waiting and polling. Request payloads and response content are not retained in results.

Run server/source telemetry collection over the same run interval. Save source pool utilization, queue depth, ingestion lag/DLQ, export progress, host/pod CPU/RAM, backend errors, physical rows/bytes scanned where available, and OTLP traces. Missing metrics remain `null`; the client does not invent server measurements.

Compare identical source snapshots, model/index settings, policy revisions, quotas, deployment images and traffic mixes. Use warmup, repeated trials, steady-state intervals and independent load generators for capacity conclusions. Record outages and overload rejections instead of dropping failed samples.
