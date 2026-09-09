# Enterprise Governed Data Retrieval Platform

A shared Data Product and policy layer for structured APIs, keyword/vector/hybrid retrieval, RAG, MCP, logical SQL ingress and asynchronous exports.

The production runtime uses PostgreSQL control state, bounded connectors, durable workers, canonical chunk authorization and OIDC. The reference runtime remains an explicit local-development option. Production environment and large-scale qualification are tracked separately from implementation.

## Review and deployment

- [Architecture assessment](ARCHITECTURE_ASSESSMENT.md)
- [Target architecture and implemented boundaries](TARGET_ARCHITECTURE.md)
- [Production readiness and remaining gates](PRODUCTION_READINESS_CHECKLIST.md)
- [Actual baseline/new performance results](PERFORMANCE_RESULTS.md)
- [Migration and compatibility](MIGRATION_PLAN.md)
- [Deployment guide](enterprise_delivery/platform_v1/DEPLOYMENT.md)
- [Security configuration](enterprise_delivery/platform_v1/SECURITY.md)
- [Architectural decisions](enterprise_delivery/platform_v1/docs/adr/)

## Local verification

```bash
cd enterprise_delivery/platform_v1
python -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.lock
PYTHONPATH=. pytest -q
cd frontend
npm ci
npm run build
npm run check
```

PostgreSQL, MySQL/MariaDB and OpenSearch integration tests run when their test endpoints are configured; CI provisions isolated services. They verify correctness, not production capacity.

## Reference demo

```bash
cd enterprise_delivery/platform_v1
docker compose up --build
```

The compose file explicitly selects reference mode. API: `http://localhost:8080/docs`; Control Hub: `http://localhost:5173`. Sample dashboard figures are labeled. The production image defaults to `production_app:create_production_app` and requires real configuration.

No billion-record/chunk or overall performance superiority claim is made. One million actual local rows were benchmarked; read the performance report before interpreting those measurements.
