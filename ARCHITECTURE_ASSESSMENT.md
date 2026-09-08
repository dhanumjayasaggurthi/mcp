# Architecture assessment

Baseline: `840fa2ef38deb7271dc274815d2f60f0813acd23`, target `enterprise-data-platform-clean`; inspected 2026-09-08. All 60 tracked files were retrieved at that immutable commit. There is no AGENTS.md. The branch is a standalone v1 Python package plus a Control Hub and legacy patch files; the legacy host application/connectors are absent.

## Evidence and strengths

The 26 existing v1 tests pass locally on Python 3.12.13. Legacy suite: 9 pass; one cannot run until the TypeScript CLI is installed. This is the pre-change baseline, not production validation. Existing SQLAlchemy expressions parameterize values, keyset ordering appends identity fields, policy has explicit-deny precedence, reference startup requires opt-in, RRF is deterministic, and export submission does not read source rows. Preserve these contracts and UI.

## Gap matrix (recorded before implementation)

Paths below are relative to `enterprise_delivery/platform_v1/enterprise_data_platform` unless qualified. Severity describes the starting branch.

| Current implementation | Bottleneck / risk | Required change | Files affected | Acceptance test | Performance / security gate |
|---|---|---|---|---|---|
| `catalog.py`, `control_state.py`, `policy.py`: dictionaries and RLocks | Critical: restart loss, divergent replicas, stale policy | Transactional SQL authoritative store, revisions/history, durable policy reads | durable.py, migrations, policy.py, production_app.py | Two store instances; restart; stale update rejected | No cache-only authorization; bounded list/transaction |
| `services.py`: policy union and caller filters | Critical: field/row grant cross product; inference via restricted filter/sort | Conservative row/column grant composition; validate user predicates separately | policy.py, query_validation.py, services.py | Cross-tenant and hidden/masked field probes | Every entry point enforces identical decisions |
| `cursor.py`: dataset/version signature only | High: cursor replay across principal/filter; non-JSON positions | Query/security scope binding, typed values, encrypted production cursors | cursor.py, services.py | Tamper, principal/filter change, timestamp/decimal continuation | No hidden ordering value exposure in production |
| `services.py`: exact count uses QUERY | High: unbudgeted full scan | Separate exact-count policy capability | models.py, services.py | Ordinary query cannot count | No automatic COUNT |
| `sqlalchemy_backend.py`: generic NULLS LAST, unbounded metadata cache | High: MySQL incompatibility, stale bindings, no deadline | Dialect capability profiles, bounded cache, statement timeout, streamed bounded fetch | connectors.py, sqlalchemy_backend.py, cache.py | Compound NULL pagination; dialect compilation; pool exhaustion | No deep OFFSET; pushdown and bounded pools |
| No planner or governor | Critical: overload cascades and reference routing | Governed plan + shared SQL admission leases/QPS, workload bulkheads | planner.py, execution.py, resilience.py | Two governors respect one quota; timeout recovery | No unlimited queue; unsupported plans fail closed |
| `services.py`: hydrates text without verifying canonical ACL | Critical: stale/poisoned index or hidden text sources leak data | Canonical row/ACL verification, text field policy gate, safe citations | services.py, chunks.py, retrieval.py | Poisoned candidate, stale ACL, restricted text source | Verify after multi-get; bounded candidates |
| `indexing.py`: per-chunk embedding, event-id-only checkpoint | Critical: target-version replay skipped, stale upserts, partial sink retries | Batch embeddings; durable jobs, target-scoped checkpoints, ordered versions and sink acknowledgement | indexing.py, ingestion.py, jobs.py, search.py | Duplicate/out-of-order/delete/partial failure | Never complete until all sinks acknowledge; stale-worker fencing |
| Search/vector/chunks only in memory | Critical: no distributed deployment path | OpenSearch BM25 and filtered ANN; durable canonical store; explicit index contracts | search.py, chunks.py | Mock HTTP contracts + optional real cluster integration | ACL prefilter; partial shard/bulk failure rejected |
| `exports.py`: process-local jobs + callback | Critical: restart loss, unmasked fields and principal context absent | SQL queue + workers, policy recheck, streaming partitions, object manifest | jobs.py, durable_exports.py, workers.py | Restart, retry, cancellation, mask/revocation, checksums | No export reads in API; bounded rows/bytes |
| Auth injection only; no durable audit | Critical: no deployable identity/audit binding | Verified JWT/JWKS, mounted/Vault-style secret boundary, append-only audit | identity.py, durable.py, production_app.py | Wrong issuer/audience/algorithm; audit on allow/deny | No trusted caller identity headers or secret telemetry |
| MCP describe lacks policy evaluation; agent scope absent downstream | High: metadata leak and missing audit context | Shared authorization for describe, propagated agent and budgets | mcp_facade.py, models.py | Revoked principal cannot describe; agent scope enforced | Permissions cannot exceed workload identity |
| Legacy rows permits unlimited OFFSET | High: full source scans; standalone legacy host missing | Hard legacy cap + aliases-to-v1 compatibility router | ../legacy_patch/data_api.py, compatibility.py | Old payload keys; cursor route; offset cap | No generated bearer URLs |
| No SQL consumer surface | High: potential parallel authorization path | Restricted SQL AST translation to structured request; gateway integration boundary | sql_gateway.py | Reject joins/subqueries/DDL/functions/physical names | Same service, policy, audit; explicit supported subset |
| Docker launches reference app; no k8s or instrumentation | Critical: unsafe production default and no HA operation | Separate production factory/workers, probes, OTel, k8s assets | production_app.py, observability.py, Dockerfile, deploy/ | Fail-closed config, startup/readiness, manifest validation | Non-root; external dependencies; drain and bounded worker counts |
| No reproducible performance evidence | High: cardinality counters mistaken for validation | Synthetic profiles, measured baseline/new, integration/fault gates | benchmarks/, tests/, PERFORMANCE_RESULTS.md | Same hardware/data/config comparison | 100M/1B remain targets until executed |

## Architecture direction

Keep PlatformService as the policy boundary. Inject durable catalog/policies/control, capability-based connector routing, admission control, audit and search adapters. Use PostgreSQL for control metadata, leases and durable queue; separate canonical storage and source data from control metadata. Keep reference implementations explicitly test-only. Independent API, export and ingestion workers share no correctness-critical Python state. Unsupported federation, acceleration or SQL constructs must be rejected rather than silently evaluated in application memory.

The starting branch is a useful reference core, not a production-ready platform. Completion status and unexecuted external gates are tracked separately in PRODUCTION_READINESS_CHECKLIST.md.
