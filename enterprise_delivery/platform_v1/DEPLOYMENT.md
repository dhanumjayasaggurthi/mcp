# Deployment and operations

The production entry point is `enterprise_data_platform.production_app:create_production_app`. It uses PostgreSQL for authoritative control state, admission and durable jobs. API, indexing and export processes run independently. Kubernetes manifests bind external dependencies; they do not provision an HA database, identity provider, search cluster or bucket.

See the root [readiness checklist](../../PRODUCTION_READINESS_CHECKLIST.md), [migration plan](../../MIGRATION_PLAN.md), [security configuration](SECURITY.md) and [schema migration](migrations/README.md) before admitting production traffic.

## 1. Verify and build

Use Python 3.12 and Node 22. From this directory:

```bash
python -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.lock
PYTHONPATH=. pytest -q tests
python -m compileall -q enterprise_data_platform
cd frontend
npm ci
npm run build
npm run check
cd ..
docker build -t edp-runtime:reviewed-release .
kubectl kustomize deploy/kubernetes/base > /tmp/edp-base.yaml
kubectl kustomize deploy/kubernetes/canary > /tmp/edp-canary.yaml
```

The image installs `requirements.production.lock`, runs as UID/GID 10001, and starts the production factory with a 64-request process concurrency limit. Pin the promoted image to its registry digest in an environment overlay. Scan that exact image and its dependencies in the deployment pipeline.

CI provisions PostgreSQL 16, MySQL 8.4, MariaDB 11.4 and OpenSearch 3.2.0 for correctness tests. Optional local integration endpoints are `EDP_TEST_POSTGRES_DSN`, `EDP_TEST_SQL_DSN` and `EDP_TEST_SEARCH_URL`. They must refer to isolated test services: the tests create tables/indexes. CI's isolated MySQL/OpenSearch transport settings do not qualify production TLS or authentication.

## 2. Configure dependencies and identity

All processes need a mounted secret directory readable by UID/GID 10001. Provision secret values through the environment's secret manager; source registration JSON contains references only. See `deploy/kubernetes/base/config.yaml` for nonsecret placeholders.

| Configuration | Requirement |
|---|---|
| `EDP_SECRET_DIR` | Default `/run/secrets/edp` |
| `EDP_CONTROL_DSN_REF` | Mounted reference, normally `file://control-dsn`; PostgreSQL with a bounded pool and `verify-full` TLS |
| `EDP_CURSOR_SECRET_REF` | Normally `file://cursor-key`; shared random material of at least 32 bytes |
| `EDP_OIDC_ISSUER`, `EDP_OIDC_AUDIENCE`, `EDP_OIDC_JWKS_URL` | Required by the API; credential-free HTTPS issuer/JWKS endpoints |
| `EDP_SEARCH_URL`, `EDP_SEARCH_TOKEN_REF` | Enable OpenSearch keyword/vector access; authenticated HTTPS |
| `EDP_EMBEDDING_URL`, `EDP_EMBEDDING_TOKEN_REF` | Enable the embedding provider's `/embeddings` protocol |
| `EDP_EMBEDDING_PROFILES` | JSON mapping of registered profile IDs to immutable approved model versions |
| `EDP_RERANK_URL`, `EDP_RERANK_TOKEN_REF` | Optional provider's `/rerank` protocol; only authorized candidate text is sent |
| `EDP_EXPORT_BUCKET`, `EDP_EXPORT_KMS_KEY_ID` | Enable S3 exports; workload IAM, private bucket and KMS required |
| `EDP_EXPORT_RETENTION_SECONDS` | Default 86400; accepted range 300–604800 |
| `EDP_CONTROL_POOL_SIZE` | Default 10 per process; `max_overflow=0` |
| `EDP_QUEUE_CAPACITY` | Default 10000 outstanding jobs |
| `EDP_RESOURCE_LIMITS` | JSON fields from `governor.Limits`; shared SQL enforcement |
| `EDP_ENGINE_NAME` | Display name for the governed planner |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Approved HTTPS OTLP/HTTP collector; absent means no exporter |
| `OTEL_SERVICE_NAME`, `EDP_RELEASE` | Service/release identifiers for the environment |

SQL-only deployments omit search, embedding, reranking and export variables, and omit their workers. The API does not require those services unless configured; requested capabilities without a backend fail closed. The supplied base ConfigMap enables the full topology with deliberately invalid placeholders, so it requires an environment overlay before use.

Mount database CA certificates as required by the driver. Set the migration and runtime database roles' default `search_path` to the same dedicated application schema. Do not rely on overriding connection `options`; the factory sets mandatory SQL timeouts. Source pools default to eight connections **per process per active source**. At 12 API replicas plus workers, review the aggregate connection ceiling against every source and control database.

Default shared limits reserve interactive capacity: global 200, background 40, tenant 40, client 20, principal 10, source 20, workload 40 and client rate 100 requests/second. Background tenant/source work is additionally capped at one quarter of the corresponding limit. Tune from measured source capacity, not pod count alone. Admission rejects overload with 429 and `Retry-After`; it does not add an unbounded waiting queue.

Embedding calls use at most 64 texts per batch with profile/version-scoped caching. Reranking permits at most 1000 candidates, 20000 characters per query/document and 1 MB of encoded request data; an oversized request fails before provider invocation. Qualify the provider's own token/rate limits and retrieval quality.

## 3. Migrate and bootstrap

1. Create the dedicated schema and separate DDL/runtime roles. Provision `edp-migration-secrets` with the DDL owner's `control-dsn`, and `edp-runtime-secrets` with the runtime DSN, cursor key and enabled backend references. Do not put secret values into the repository.
2. Apply the namespace and completed ConfigMap. Render `deploy/kubernetes/migrate.yaml` with the same immutable image as the release, apply it, then wait for `job/edp-migrate-v1` to complete. Alternatively run `python -m enterprise_data_platform.workers migrate` with the DDL secret mount.
3. Apply the runtime grants from `migrations/README.md`. API/workers must not own tables or have DDL/audit mutation rights. Startup verifies schema version 1; it never automatically runs migrations.
4. Start the API and verify `/livez` and `/readyz`. Readiness checks the authoritative control store. It is not an all-source/search/model health verdict.
5. Authenticate an administrator with both `data-platform-admin` group and `edp:admin` OAuth scope. Register sources, Data Products, policies, clients, optional agents and aliases through `/v1/control/*`. No public bootstrap or hardcoded production account is provided.
6. Register least-privilege source roles, tenant fields and filterable/sortable logical fields. Exercise a consumer query plus cross-tenant and hidden-column denials before exposing a product.

For example, a source registration can use this body at `PUT /v1/control/sources/orders-db`:

```json
{
  "kind": "postgres",
  "secret_ref": "file://orders-db-dsn",
  "pool_size": 8,
  "statement_timeout_seconds": 15,
  "max_scan_rows": 1000000
}
```

Use the returned revision on updates. Data Products reference `source_id: "orders-db"`; consumers see logical product/field aliases. PostgreSQL uses non-executing EXPLAIN estimates for scan admission. Other dialects require an exercised native source scan governor before setting `native_scan_governor: true`; an uninspectable cost is otherwise rejected.

## 4. Run and scale

Equivalent process commands, with the production configuration already supplied:

```bash
make production
# In separate processes/containers:
make indexing-worker
make export-worker
```

The base includes three API replicas, two workers of each kind, API HPA 3–12, resource/temporary-volume limits, readiness/liveness probes, zone spreading, anti-affinity and PDBs. Worker processes stop claiming new jobs on SIGTERM and allow the current bounded unit to finish. An interrupted owner loses its lease; retries use fencing and idempotent sink writes. Worker readiness signals successful initialization, not continuous dependency health. Monitor job age/progress independently. Worker replicas are manually sized; queue-driven autoscaling is an environment extension.

Use a completed environment Kustomize overlay. Add the exact managed dependency CIDRs or an approved egress gateway to the default-deny NetworkPolicy. Namespace labels `edp-gateway=true` and `edp-dependency=true` are trust decisions controlled by cluster administrators. Allow the OIDC/JWKS, PostgreSQL, enabled source/model/search, KMS/S3/IAM and collector paths actually needed by that workload.

The service account disables automatic token mounting. Configure the cloud's explicit workload identity projection/annotations where S3/KMS requires them; never fall back to static cloud keys in the image. Supply the external ingress/service mesh, TLS/mTLS, OIDC host integration and traffic policy in the target environment. These vendor-specific assets are not fabricated in the base.

The canary overlay creates a separate `edp-api-canary` Deployment and Service using shared control state. Set a distinct candidate image in that overlay and route a measured gateway percentage to its Service. Stable/canary Deployment selectors are disjoint. Kubernetes render success alone does not establish a safe rollout. Roll back gateway traffic on policy, error, tail-latency or queue regressions. Keep schema changes backward compatible; do not drop durable tables during application rollback.

## 5. Indexing and exports

Before ingestion, provision the candidate version with `OpenSearchSink.provision` for each required keyword/vector sink using approved shard/replica/vector settings. Capture an actual source snapshot/CDC boundary. Publish bounded event pages at `/v1/control/ingestion-pages`; persist the returned source resume token only after the transaction succeeds. The publication checkpoint and highest processed checkpoint are distinct. Source-specific CDC readers remain integrations.

Start indexing workers, verify both sinks and canonical hydration, then submit representative quality evidence through the coordinated promotion controller. Keep old indexes through the rollback window. Do not rename physical indexes or edit the routing row outside the controller.

Apply the private bucket/KMS policy and `deploy/object-lifecycle.json` before enabling export workers. The default object lifecycle expires export data after eight days, aborts incomplete multipart uploads after one day and cleans noncurrent versions after one day. API retention may expire earlier. Verify the prefix and bucket versioning behavior in the target account.

Exports run as durable jobs with bounded governed pages/parts, policy masking, row/byte limits and checksum manifests. Reauthorize each page and download. No cross-page source snapshot is created; use an immutable source product for repeatability. An expired submitting identity ends its export grant. Downloads expire within five minutes and cannot extend artifact retention. `Idempotency-Key` identifies equivalent submissions; changed payloads with the same key conflict.

## 6. Observe and recover

Export OTLP request/adaptor spans and request latency/count, active request, returned row/byte and cache instruments to restricted dashboards. Correlate `x-request-id`/`x-trace-id` with sanitized request logs and durable audit. A 401/403/422 response preserves a trace identifier and does not echo validation input. Collect database/pool utilization, oldest queued age, DLQ, retries, checkpoints, export progress, source scans and model latency separately. The Control Hub shows known control state; missing metrics are not fabricated as healthy or zero.

| Failure | Expected behavior and operator action |
|---|---|
| Control PostgreSQL unavailable | Readiness fails; new authoritative authorization/admission fails. Restore/fail over the database, verify leases and audit, then admit traffic. No local policy bypass. |
| Source outage/pool exhaustion | Bounded read retries/deadlines and circuit breaker; inspect source health, pool ceiling and native timeout. Do not add retries without a deadline. |
| Search/model throttling or partial shards | Bounded retries; partial search is rejected. Keyword fallback runs only when the product explicitly permits it. |
| Worker terminated after one sink write | Lease expires; retry converges by version/sequence and cannot mark success until required sinks and canonical state agree. Inspect DLQ before replay. |
| Export upload interruption | Attempt cleanup and failure/retry; no successful manifest after partial writes. Lifecycle handles orphaned multipart state. Verify real bucket cleanup. |
| Poison event / repeated failure | Job reaches failed state after bounded attempts. Fix the cause, inspect redacted admin job status, then explicitly replay `/v1/control/jobs/{id}/replay`. |
| Changed policy/client/agent | Authoritative reads apply the change to subsequent decisions. Existing cursors/policy-bound exports may stop. Already issued short-lived download URLs expire naturally. |

Before production, exercise worker kill/restart, control failover/restore, source throttling, search partial writes, policy revocation, object-storage interruption and rolling upgrades in a disposable target environment. The CI failure-contract tests cover deterministic correctness cases; actual infrastructure chaos and SLO results remain readiness gates. Use the [benchmark harnesses](benchmarks/README.md) for matched workload trials and retain failures as evidence.

## Reference demo only

`docker compose up --build` explicitly selects the reference app and sample data. For separate local processes, use `EDP_REFERENCE_MODE=true` with `reference_app:app`, a test cursor secret and `VITE_REFERENCE_MODE=true` for the frontend. Never send production traffic to that header-based demo identity path. The production Control Hub build integrates a host-provided OIDC access-token callback described in the migration plan.
