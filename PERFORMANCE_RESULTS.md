# Performance results

## What was measured

The local source contained **1,000,000 actual rows**, inserted in batches of 5,000 and independently verified with an explicit setup COUNT outside the timed request path. Each row contains an integer ID, one of ten tenants and a short synthetic body. The source has a `(tenant, id)` index. Source cardinality is not inferred from a displayed counter.

Three baseline/new pairs used the same SQLite database, Python **3.12.13**, dependency environment, page request and eight-connection pool. Each implementation ran 120 requests at concurrency 1, 4 and 16 per trial: 360 observations per concurrency and implementation. An initial request creates the cursor and warms schema reflection; measured requests repeatedly fetch the second 100-row page for one tenant. Trials ran baseline then new, with no simultaneous baseline/new load. This was a shared workstation, not a dedicated benchmark host.

The baseline core is the supplied branch at `840fa2ef38deb7271dc274815d2f60f0813acd23`. The new core query path is the implementation published in `933a7bd4552f82f80636b2cb7d4e45ed6376b327`. Raw trial files record the interpreter and scope. The benchmark deliberately retains reference catalog/policy wiring to isolate the SQL/query changes; it does **not** exercise production SQL admission/audit overhead, OIDC, network latency or a distributed source.

## Paired results

Values below are medians of the three per-trial measurements, not a pooled percentile or a statistical confidence interval. Latency columns show baseline / new in milliseconds.

| Concurrency | Baseline req/s | New req/s | Throughput change | p50 ms | p95 ms | p99 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1330.2 | 990.9 | -25.5% | 0.634 / 0.783 | 1.027 / 1.010 | 2.203 / 1.303 |
| 4 | 496.4 | 498.5 | +0.4% | 8.120 / 7.302 | 13.930 / 15.310 | 15.653 / 19.282 |
| 16 | 193.1 | 245.6 | +27.2% | 44.126 / 35.419 | 606.804 / 444.380 | 610.233 / 472.305 |

All six paired runs completed with zero recorded errors and timeouts. Median peak process RSS across the concurrency levels was roughly 41–43 MiB for baseline and 45–46 MiB for new. CPU seconds, peak RSS and pool limits are retained per trial in the raw JSON. Pool limit is a configured maximum, not a measured server connection time series. There is no queue in this local benchmark.

The results are mixed. Single-request concurrency loses throughput, concurrency 4 is similar, and concurrency 16 improves in these trials. Additional policy/cursor validation and bounded cache logic have costs; attributing the observed difference to any one code change would require profiling. Small sample sizes, SQLite contention, host scheduling and page-cache effects limit interpretation. **These results do not establish an overall speedup or production capacity.**

## Reproduction and larger profiles

Run the same checked-in harness against separate baseline/new checkouts, copying the harness into the baseline checkout if necessary:

```bash
PYTHONPATH=. python benchmarks/structured.py --rows 1000000   --database /absolute/path/benchmark.db --label new   --output benchmarks/new-local.json
```

Use the same interpreter, lockfile, hardware, database, page size, request count and concurrency settings. Preserve all trials and errors. The harness now rejects a reused database whose actual row count differs from `--rows`. Exploratory single runs in `benchmarks/baseline.json` and `benchmarks/new.json` predate the paired runs; the `benchmarks/runs/` files are the comparison evidence.

`benchmarks/seed_postgres.py` creates 1M, 100M or 1B synthetic source profiles in bounded transactions and supports resume by committed ID. `benchmarks/http_load.py` drives structured query, cursor, metadata, keyword, vector, hybrid, retrieval/hydration, indexing completion and export completion at configurable concurrency. Its profile label is an operator declaration, not verification of remote cardinality. See the benchmark README for token handling, provisioning, telemetry and write-job isolation.

The end-to-end harness must be paired with server/source metrics: CPU, memory, active connections, queue depth, DLQ, source/embedding latency, rejected work and physical scan metrics where available. Client RSS/CPU are explicitly labeled client-side. Capacity work must include tenant skew, large text, selective/unselective predicates, mixed interactive/background traffic, long-duration steady state and failure recovery.

## What has not been measured

No 100M or 1B source was provisioned. There is no measured billion-chunk canonical/search cluster, multi-region throughput, managed-source failover SLO, production embedding/reranking throughput, or large S3 export rate. Real PostgreSQL/connector CI verifies correctness contracts; it is not a capacity benchmark. These remain target-environment gates in the readiness checklist.
