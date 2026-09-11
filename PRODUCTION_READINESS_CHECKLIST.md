# SmartHub MCP & Agentic Gateway production readiness and evidence

This is a production runtime implementation with deployment qualification still required. Passing local/CI tests does not establish the capacity, SLO, failover behavior or compliance of an unprovisioned enterprise environment. The evidence below distinguishes implemented behavior, tested integrations and remaining work.

## Implemented and exercised

- [x] The original branch was inspected before implementation; baseline architecture risks are recorded in [ARCHITECTURE_ASSESSMENT.md](ARCHITECTURE_ASSESSMENT.md).
- [x] Durable catalog, policy, client, agent, guardrail and index registrations with revisions, semantic dataset versions and conflict detection.
- [x] PostgreSQL migrations and repeated migration execution; append-only audit/history protections.
- [x] Shared concurrency admission and unique `SKIP LOCKED` job claims under real PostgreSQL concurrency tests.
- [x] Durable queue capacity, idempotency, cancellation, retry fencing and restart contracts.
- [x] Atomic source publication checkpoints; partial publication failures roll back both events and resume tokens.
- [x] Indexing duplicate/out-of-order/tombstone handling; a failed required sink cannot commit canonical data or success.
- [x] Governed SQLAlchemy query pushdown, compound NULL keysets, scoped encrypted cursors and bounded source pools.
- [x] PostgreSQL estimates and estimated scan rejection; exact count requires a separate permission.
- [x] Aggregation runs at the source after tenant/field/policy checks; count measures require exact-count permission.
- [x] OIDC signature, issuer, audience, expiry and header-spoofing tests; client and agent scope enforcement.
- [x] Final canonical ACL and tenant verification before text is exposed or sent to a reranker.
- [x] Private asynchronous Parquet/CSV/JSONL export implementation with masking, manifests, checksums, progress, cancellation, cleanup and short-lived downloads.
- [x] Object-upload interruption test verifies cleanup and prevents success after a partial write.
- [x] Restricted SQL ingress and legacy aliases delegate to the shared governed service; deep offsets are rejected.
- [x] MCP dataset/capability/tool-call/top-k/context limits remain narrower than the workload identity.
- [x] OpenTelemetry-compatible request/adaptor spans, request/row/byte/cache metrics and sanitized structured request logs.
- [x] Production Docker image, base/canary Kustomize rendering, separate worker Deployments, API HPA, PDBs, probes and network boundaries.
- [x] Frontend build/type check; production dashboard has no fabricated operational metrics or billion-record fallback figures.
- [x] One million actual SQLite rows, three paired baseline/new runs at concurrency 1, 4 and 16; raw measurements committed.

CI at commit `94f32fd8fd8981fb03880c7acaec1f0c2d333534` passed all six jobs: [run 34302069264](https://github.com/dhanumjayasaggurthi/mcp/actions/runs/34302069264). Its Python job passed **75 platform tests** (two optional connector tests skipped there) and **10 legacy tests**. Dedicated jobs passed the real MySQL 8.4, MariaDB 11.4 and OpenSearch 3.2.0 contracts; frontend build/type checks, base/canary rendering and the production image gates also passed. Subsequent changes add error-envelope/reranker regression cases and scope the stable selector; consult the [PR's final-head checks](https://github.com/dhanumjayasaggurthi/mcp/pull/3/checks) for those changes rather than treating this historical run as their validation.

## Deliverable map

| Requested deliverable | Repository evidence |
|---|---|
| Architecture assessment / target | Root `ARCHITECTURE_ASSESSMENT.md`, `TARGET_ARCHITECTURE.md` |
| Major decisions | `enterprise_delivery/platform_v1/docs/adr/001` through `006` |
| Concrete runtime / source framework | `production_app.py`, `connectors.py`, `sqlalchemy_backend.py`, `rest_connector.py` |
| Durable control / migrations | `durable.py`, `migrations/README.md`, PostgreSQL integration tests |
| Durable queue / exports | `jobs.py`, `durable_exports.py`, independent `workers.py export` |
| Distributed indexing framework | `ingestion.py`, `chunks.py`, `search.py`, independent indexing workers |
| Search / retrieval / MCP | `search.py`, `retrieval.py`, `services.py`, `mcp_facade.py` |
| Governed planner / admission | `planner.py`, `execution.py`, `governor.py`; strategy limits below |
| Consumer aliases / SQL integration | `compatibility.py`, `sql_gateway.py`, ADR 006 |
| Identity / security / telemetry | `identity.py`, `observability.py`, `SECURITY.md` |
| Kubernetes / recovery | `deploy/kubernetes`, `DEPLOYMENT.md` |
| Load / failure / security tests | `benchmarks/`, `tests/test_durable_security.py`, `test_worker_adapters.py`, `test_production_boundaries.py`, integration tests |
| README / migration / readiness / measured results | Root Markdown documents and committed raw paired benchmark runs |

Module paths in this table are under `enterprise_delivery/platform_v1/enterprise_data_platform`; documentation, test and deployment paths are under `enterprise_delivery/platform_v1` unless explicitly rooted. Presence is not a claim that every strategy or external adapter has been qualified; the limits and gates below are part of the delivery.

## Deployment gates

| Gate | Required evidence before production traffic |
|---|---|
| PostgreSQL HA | Actual failover under requests/claims, backup restore, RPO/RTO, pool drain and role separation |
| MySQL/MariaDB | Real dialect CI plus managed-service TLS, read-only grants, timeout and native scan governor qualification |
| Snowflake/Denodo/generic drivers | Installed driver versions, real query/null/timeout/cancellation tests, pooling and source-cost controls |
| REST upstream | Governed-page protocol implementation, pinned-host TLS validation, response bounds and mandatory-filter parity |
| OpenSearch | Real bulk/ANN CI plus managed-cluster TLS/auth, shard/replica sizing, node-loss and throttling exercises |
| Embedding/reranking | Provider/model/version approval, real batching and rate-limit behavior, token/character budgets, quality and privacy review |
| Object storage | Workload IAM, KMS permissions, lifecycle policy, real multipart interruption, signed URL expiry and revocation tests |
| OIDC / mTLS | Tenant/client/agent claim ownership, key rotation, issuer outage, ingress trust boundary and Control Hub OIDC host integration |
| Audit | Application has no DDL/audit mutation privilege; protected audit export and immutable archive retention are provisioned |
| Retrieval quality | Representative judged dataset, recall/NDCG/citation and cross-tenant tests on active and candidate versions |
| Operations | OTLP dashboards/alerts, queue/DLQ and export progress collection, per-source pool metrics, on-call procedures |
| Scale/SLO | End-to-end 1M/100M/1B profiles where infrastructure permits; isolated CPU/RAM/connections/queues and steady-state tail latency |
| Rollout | Image digest, dependency/security scan, egress overlay, service accounts, secrets and canary traffic policy reviewed in the target cluster |

## Explicit implementation limits

These are not satisfied merely by an interface or an enum value:

- A general federated optimizer, cross-source joins, parallel partition query execution, materialized acceleration and result-cache execution are not implemented. The current planner performs governed capability/modality/deadline routing and PostgreSQL cost gating.
- The SQL HTTP ingress is implemented. A standard JDBC/Flight SQL frontend is an integration design, not a shipped wire-protocol server. See ADR 006.
- Connector-owned snapshot boundaries and CDC capture remain source integrations. The event bridge and resumable publication contract are implemented; there is no universal log reader for every source.
- Keyword/filtered ANN, fusion, semantic reranker integration, canonical verification, deduplication and lexical diversification are implemented. General language-specific analyzers, field/source/recency boosts and semantic-vector MMR configuration are not yet provided.
- Schema and embedding caches are active. Catalog/policy/capability/plan/result cache key classes are defined; they are not all active caches. Current authorization deliberately uses authoritative reads without a cache-invalidation dependency.
- PostgreSQL canonical chunks are partitioned by dataset; no billion-chunk scale claim is made. The shared queue counter and control/chunk database need capacity qualification or a different durable SPI implementation at large scale.
- Deadlines/cancellation are cooperative and depend on bounded drivers. There is no universal forced cancellation mechanism for arbitrary JDBC/ODBC drivers.
- Per-job row/byte limits and concurrency/QPS quotas are enforced. Fine-grained per-tenant network bandwidth and physical rows/bytes-scanned accounting are not implemented.
- Exports use keyset pages without a cross-page source snapshot. Mutable sources require an immutable snapshot product for repeatable export semantics. Export grants do not outlive the submitted identity's expiry.
- SQL append-only triggers protect application access; a database superuser can still alter them. An external immutable audit archive is a deployment requirement.
- No target-cluster failover, live managed-source/search/object-store trial, 100M run or 1B run has been performed in this work environment.

These gates keep the PR in draft until the owning environment can provide the required infrastructure evidence. They do not prevent review of the implemented code or execution of the supplied test and deployment harnesses.


## RDH schema implementation

Native PostgreSQL chunk retrieval, compound typed filters (including arrays/JSONB), governed exact-ID lookup, filter discovery, administrator source inspection/drafting, physical binding validation, online index-plan generation and a PostgreSQL-only deployment overlay are implemented. See [RDH onboarding](enterprise_delivery/platform_v1/RDH_ONBOARDING.md) for deployment and consumer examples.

Native source tests use a synthetic PostgreSQL/pgvector corpus matching the supplied chunk layout, including an unbounded vector column with an explicit dimension expression index. Source metadata is inspected rather than inferred from table names. Approval, tenancy and ACL fields added to test fixtures are synthetic and do not assert that those columns exist in RDH.

Still required from the target environment: authoritative document/chat access and approval rules; real Q/HAQ-to-EDMS mappings; matching stored/query embedding model and dimensions; source-specific CDC capture where replication is needed; credentials, TLS/OIDC and network deployment; representative data-volume, concurrency, recall and HA qualification. Existing ingestion status must not be treated as document approval. Native reads avoid a replicated search copy but do not make source ingestion/embedding updates atomic or provide a multi-request database snapshot.
