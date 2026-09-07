# Enterprise Governed Data Retrieval Platform v1 — Core Implementation

This package is the implementation foundation for the target RDH/RegAssist architecture. It deliberately separates the **control plane** from the **data plane** and gives REST, RAG and MCP one shared policy/retrieval core.

## Implemented core

- Data Product catalog and versioned contracts.
- ABAC/RBAC-style policy decision point with explicit deny, field controls, masking, tenant isolation and request caps.
- HMAC-signed dataset/version-scoped cursors; no public v1 `offset` parameter.
- Structured query service with deterministic identity tie-breakers.
- SQLAlchemy pushdown backend using parameterized filters and keyset continuation.
- Keyword, vector and hybrid retrieval contracts; weighted reciprocal-rank fusion.
- RAG clear-text chunk hydration with record/chunk source metadata.
- Async export queue contract; API process does not materialize full exports.
- Idempotent snapshot/CDC indexing core targeting a candidate index version.
- Retrieval quality evaluation and zero-downtime index promotion gates.
- Control Hub APIs for datasets, policies, clients, agents, guardrails and indexes.
- Runtime guardrail engine.
- MCP facade that calls the same governed service layer and checks registered agent identity/scope.
- J&J-style Control Hub frontend foundation in `frontend/EnterpriseControlHub.jsx`.
- Bounded operational dashboard read model for service health, latency, error,
  policy, deployment, consumer, MCP, alert and audit aggregates.

## Important deployment boundary

The included in-memory stores/search adapters are test/reference adapters only. Production must bind the interfaces to the organization's HA control database, enterprise identity system, source warehouses, keyword engine, vector database, CDC/queue stack, object storage/export workers and observability platform.

The displayed dashboard values are reference data supplied by
`InMemoryOperationsProvider`. Production must inject an `OperationsProvider`
backed by pre-aggregated telemetry and immutable audit storage. Dashboard reads
never scan source records or vector collections. Large displayed counts are not
evidence of a completed billion-record load test; run the performance,
resilience and accuracy gates against the selected enterprise backends before
promotion.

## Non-negotiable scale rules represented in code

- No deep client offset in v1 query contracts.
- Stable cursor pagination appends unique identity fields.
- Exact count is not a default query behavior.
- Metadata/tenant policy filters are applied before source/index execution.
- Large exports are asynchronous jobs.
- Reindexing writes a candidate version; active index is not mutated in place.
- Candidate promotion requires retrieval quality, citation coverage, latency, error, freshness and replica gates, then canary traffic.
- MCP is a transport facade, never an alternate database access path.

## Run gates

```bash
cd platform_v1
PYTHONPATH=.:tests pytest -q tests
python -m compileall -q enterprise_data_platform
cd frontend
npm install
npm run check
npm run build
```

Use the pinned dependency versions in `frontend/package.json`; do not replace
them with floating `latest` ranges. Generate and commit the lockfile from an
approved registry before switching CI and release builds to `npm ci`.
