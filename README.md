# Enterprise Governed Data Retrieval Platform

Governed enterprise retrieval platform for structured, keyword, vector, hybrid, RAG and MCP-enabled access, with centralized Control Hub governance.

## Development branch

Use `enterprise-data-platform-clean` for the complete, source-visible deployment candidate.

## Local DEV

```bash
cd enterprise_delivery/platform_v1
docker compose up --build
```

API: `http://localhost:8080/v1/health`  
Control Hub: `http://localhost:5173`

See `enterprise_delivery/platform_v1/DEPLOYMENT.md` for validation and deployment details.
