# Deployment and Test Guide

## What is deployable now

The repository stores the complete implementation starter in `artifacts/Enterprise_Data_Retrieval_Control_Hub_Implementation_Starter.zip`. Run `scripts/materialize_source.sh` after cloning to expand the source tree.

A reference runtime is included so the team can deploy and exercise the API, policy plane, Control Hub APIs, vector/keyword/hybrid/RAG contracts and zero-downtime promotion logic immediately.

The reference runtime intentionally uses in-memory stores and deterministic test embeddings. It is for DEV/integration testing only. Production still requires enterprise OIDC/workload identity, HA control storage, actual RDH connectors, distributed keyword/vector stores, durable CDC/queue/object storage and observability.

## 1. Clone and materialize

```bash
git clone https://github.com/dhanumjayasaggurthi/mcp.git
cd mcp
git checkout enterprise-data-platform
chmod +x scripts/materialize_source.sh
./scripts/materialize_source.sh
```

## 2. Run automated gates

```bash
cd enterprise_delivery/platform_v1
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[test]' uvicorn
PYTHONPATH=. pytest -q tests
python -m compileall -q enterprise_data_platform

cd ..
PYTHONPATH=. pytest -q tests
```

Expected current gate result: enterprise v1 tests and legacy remediation tests must all pass before deployment.

## 3. Run the reference API

```bash
cd enterprise_delivery/platform_v1
source .venv/bin/activate
export EDP_REFERENCE_MODE=true
export EDP_CURSOR_SECRET='replace-this-with-a-long-random-test-secret'
export EDP_CORS_ORIGINS='http://localhost:5173'
uvicorn enterprise_data_platform.reference_app:app --host 0.0.0.0 --port 8080
```

Health check:

```bash
curl -s http://localhost:8080/v1/health
```

List visible data products:

```bash
curl -s http://localhost:8080/v1/datasets \
  -H 'x-client-id: reference-consumer' \
  -H 'x-tenant: demo'
```

Structured retrieval:

```bash
curl -s -X POST http://localhost:8080/v1/datasets/regulatory-documents/query \
  -H 'content-type: application/json' \
  -H 'x-client-id: reference-consumer' \
  -H 'x-tenant: demo' \
  -d '{"select":["id","title","document_type"],"limit":10}'
```

Hybrid RAG retrieval with clear text:

```bash
curl -s -X POST http://localhost:8080/v1/datasets/regulatory-documents/retrieve \
  -H 'content-type: application/json' \
  -H 'x-client-id: reference-consumer' \
  -H 'x-tenant: demo' \
  -d '{"query":"adverse event reporting","mode":"hybrid","top_k":5}'
```

Control Hub admin API:

```bash
curl -s http://localhost:8080/v1/control/overview \
  -H 'x-client-id: control-hub' \
  -H 'x-tenant: demo' \
  -H 'x-groups: data-platform-admin'
```

## 4. Run the Control Hub UI

In another terminal:

```bash
cd enterprise_delivery/platform_v1/frontend
npm install
VITE_API_BASE_URL=http://localhost:8080 \
VITE_REFERENCE_MODE=true \
npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173`.

## 5. Docker Compose

From `enterprise_delivery/platform_v1`:

```bash
docker compose up --build
```

Endpoints:

- API: `http://localhost:8080/v1/health`
- Control Hub: `http://localhost:5173`

Stop:

```bash
docker compose down
```

## Production promotion gate

Do not deploy the reference adapters to PROD. Replace each reference adapter with the enterprise implementation and complete the remaining gates in `enterprise_delivery/IMPLEMENTATION_STATUS.md`: identity, HA control store, real source connectors, distributed keyword/vector infrastructure, CDC/queue, object storage/export workers, observability, security/load/chaos testing, golden-set retrieval accuracy, accessibility/UAT, and DEV -> QA -> PROD canary rollout.
