# Deployment and Test Guide

## Scope

The repository contains the full reviewed implementation starter plus a **reference deployment harness**. The reference harness is intentionally backed by in-memory stores and deterministic test embeddings so the team can deploy and exercise API/control-plane flows immediately.

It is **not** the production infrastructure binding. Production still requires enterprise OIDC/workload identity, HA control storage, RDH connectors, distributed keyword/vector stores, durable CDC/queue/object storage and observability.

## 1. Automated gates

### Linux/macOS

```bash
cd enterprise_delivery/platform_v1
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test,server]'
PYTHONPATH=. pytest -q tests
python -m compileall -q enterprise_data_platform
cd ..
PYTHONPATH=. pytest -q tests
```

### Windows PowerShell

```powershell
cd enterprise_delivery\platform_v1
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[test,server]"
$env:PYTHONPATH = "."
pytest -q tests
python -m compileall -q enterprise_data_platform
cd ..
$env:PYTHONPATH = "."
pytest -q tests
```

## 2. Run API reference deployment

### Linux/macOS

```bash
cd enterprise_delivery/platform_v1
source .venv/bin/activate
export EDP_REFERENCE_MODE=true
export EDP_CURSOR_SECRET='replace-this-with-a-long-random-test-secret'
uvicorn enterprise_data_platform.reference_app:app --host 0.0.0.0 --port 8080
```

### Windows PowerShell

```powershell
cd enterprise_delivery\platform_v1
.\.venv\Scripts\Activate.ps1
$env:EDP_REFERENCE_MODE = "true"
$env:EDP_CURSOR_SECRET = "local-dev-secret-change-me-123456789"
$env:EDP_CORS_ORIGINS = "http://localhost:5173"
python -m uvicorn enterprise_data_platform.reference_app:app --host 0.0.0.0 --port 8080
```

Keep this terminal running.

Smoke test from a second PowerShell window:

```powershell
Invoke-RestMethod http://localhost:8080/v1/health
Invoke-RestMethod http://localhost:8080/v1/datasets -Headers @{
  "x-client-id" = "reference-consumer"
  "x-tenant" = "demo"
}
```

FastAPI interactive API UI is available at `http://localhost:8080/docs`.

## 3. Run the Control Hub UI

### Linux/macOS

```bash
cd enterprise_delivery/platform_v1/frontend
npm install
VITE_API_BASE_URL=http://localhost:8080 VITE_REFERENCE_MODE=true npm run dev -- --host 0.0.0.0
```

### Windows PowerShell

Open a second terminal:

```powershell
cd enterprise_delivery\platform_v1\frontend
npm install
$env:VITE_API_BASE_URL = "http://localhost:8080"
$env:VITE_REFERENCE_MODE = "true"
npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173`.

## 4. Docker Compose

Docker Desktop must be installed and the Linux container engine must be running first.

Check:

```powershell
docker version
docker info
```

If those commands cannot connect to `dockerDesktopLinuxEngine`, start Docker Desktop and wait until it reports the engine is running. Then:

```powershell
cd enterprise_delivery\platform_v1
docker compose up --build
```

Then:

- API: `http://localhost:8080/v1/health`
- API UI: `http://localhost:8080/docs`
- Control Hub: `http://localhost:5173`

Stop:

```powershell
docker compose down
```

## Production promotion gate

Do not promote the reference runtime to production. Replace each reference adapter and rerun the gates in `IMPLEMENTATION_STATUS.md` before DEV -> QA -> PROD promotion.
