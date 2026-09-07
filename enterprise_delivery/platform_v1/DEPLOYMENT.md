# Deployment and Test Guide

## Scope

The repository contains the full reviewed implementation starter plus a **reference deployment harness**. The reference harness is intentionally backed by in-memory stores and deterministic test embeddings so the team can deploy and exercise API/control-plane flows immediately.

It is **not** the production infrastructure binding. Production still requires enterprise OIDC/workload identity, HA control storage, RDH connectors, distributed keyword/vector stores, durable CDC/queue/object storage and observability.

## 1. Automated gates

```bash
cd enterprise_delivery/platform_v1
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]' uvicorn
PYTHONPATH=. pytest -q tests
python -m compileall -q enterprise_data_platform
```

Legacy remediation gates:

```bash
cd ../
PYTHONPATH=. pytest -q tests
```

## 2. Run API reference deployment

```bash
cd enterprise_delivery/platform_v1
source .venv/bin/activate
export EDP_REFERENCE_MODE=true
export EDP_CURSOR_SECRET='replace-this-with-a-long-random-test-secret'
uvicorn enterprise_data_platform.reference_app:app --host 0.0.0.0 --port 8080
```

Smoke test:

```bash
curl -s http://localhost:8080/v1/health
curl -s -H 'x-client-id: reference-consumer' -H 'x-tenant: demo' \
  http://localhost:8080/v1/datasets

curl -s -X POST http://localhost:8080/v1/datasets/regulatory-documents/query \
  -H 'content-type: application/json' \
  -H 'x-client-id: reference-consumer' -H 'x-tenant: demo' \
  -d '{"select":["id","title","document_type"],"limit":10}'

curl -s -X POST http://localhost:8080/v1/datasets/regulatory-documents/retrieve \
  -H 'content-type: application/json' \
  -H 'x-client-id: reference-consumer' -H 'x-tenant: demo' \
  -d '{"query":"adverse event reporting","mode":"hybrid","top_k":5}'
```

Control Hub admin API:

```bash
curl -s http://localhost:8080/v1/control/overview \
  -H 'x-client-id: control-hub' -H 'x-tenant: demo' \
  -H 'x-groups: data-platform-admin'
```

## 3. Run the Control Hub UI

```bash
cd enterprise_delivery/platform_v1/frontend
npm install
VITE_API_BASE_URL=http://localhost:8080 VITE_REFERENCE_MODE=true npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173`.

## 4. Docker Compose

```bash
cd enterprise_delivery/platform_v1
docker compose up --build
```

Then:

- API: `http://localhost:8080/v1/health`
- Control Hub: `http://localhost:5173`

Stop:

```bash
docker compose down
```

## Production promotion gate

Do not promote the reference runtime to production. Replace each reference adapter and rerun the gates in `IMPLEMENTATION_STATUS.md` before DEV -> QA -> PROD promotion.
