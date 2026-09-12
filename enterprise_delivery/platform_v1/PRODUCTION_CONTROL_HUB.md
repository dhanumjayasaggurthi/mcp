# Control Hub implementation and release checks

This change replaces the previous static dashboard with authenticated routed
workflows. The approved visual direction uses a light canvas, J&J-inspired red
actions, colorful Lucide icons, self-hosted typography and accessible controls.
It is not a certification of J&J brand compliance or production-scale performance.

## Reference screen coverage

| Reference screens | Implementation |
| --- | --- |
| Overview; Monitoring | `/overview`, `/monitoring`: durable counts, actual audit/jobs, bounded per-replica telemetry; unavailable health is unknown |
| Sources; Data products | `/sources`, `/datasets`: paginated registries, source inspection, versioned editing |
| Onboarding Source; Object; Contract; Index plan | `/datasets/new`: inspect the selected source, generate a contract draft, edit fields, validate and request reviewable DDL |
| Product Contract; Retrieval; Validate; Simulate | `/datasets/:id`: contract, field matrix, retrieval configuration, physical validation and hypothetical access simulation tabs |
| Collections | `/collections`: durable member configuration; collection retrieval preflights all member permissions |
| Indexes | `/indexes`: typed measured quality evidence, validation, canary, promotion and rollback APIs |
| Policies; Policy editor | `/policies`: versioned schema forms and nested filter builder, including administrator-owned principal bindings |
| Guardrails | `/guardrails`: typed configuration, change review and persistence |
| Consumers; Consumer editor | `/clients`: grants, limits and revision-aware persistence |
| Agents and MCP | `/agents`: registration and authenticated Streamable HTTP MCP endpoint information |
| Playground retrieval; Collections | `/playground`: same-identity query/retrieval/export, pagination, collection requests and signed export part links |
| Jobs; Audit | `/jobs`, `/audit`: bounded keyset pagination, inspection, failed-job replay, audit filters and current-page export |

## Run and deploy

Install Python dependencies from `requirements.lock` for development or
`requirements.production.lock` for API deployment. Install frontend dependencies
with `npm ci` inside `frontend`, then run `npm run dev`; the default API proxy is
`http://127.0.0.1:8080`, configurable with `API_PROXY_TARGET`.

Use the existing production API configuration and deployment manifests. The new
frontend image is built separately:

```sh
docker build -t control-hub-ui \
  --build-arg VITE_OIDC_AUTHORITY=https://identity.example.com \
  --build-arg VITE_OIDC_CLIENT_ID=control-hub \
  frontend
```

Configure the real public OIDC client with PKCE, the exact callback
`https://<hub-host>/auth/callback`, post-logout origin, authorized scopes and API
audience. The frontend can also consume the existing host `window.edpAuth`
adapter. Access tokens remain in memory. No secret belongs in Vite build args.
Route `/v1/*` and `/mcp` on the same HTTPS ingress to the API, and all UI routes to
the frontend on port 8080. The static server deliberately does not proxy to a
guessed API hostname. Configure TLS/HSTS and identity-provider-compatible CSP at
the ingress. Production builds reject reference authentication mode.

## Schema and execution changes

- Principal bindings are accepted only in administrator-managed mandatory
  filters. Missing attributes fail closed; consumer filters cannot supply them.
- Non-null uniform-direction keysets use row comparisons; nullable and mixed
  ordering retain the fallback. PostgreSQL estimates are schema-qualified.
- Native keyword retrieval distinguishes literal trigram contains from full-text
  indexing. A trigram index does not satisfy a full-text requirement. Existing
  equivalent indexes are skipped by inspected index plans; DDL is never executed
  by the API.
- Retrieval auto mode resolves available enabled capabilities before executing.
- Admission bookkeeping is batched while retaining authoritative database locks.
  Request-local control and decision reuse does not cache grants across requests.
- MCP uses the protocol SDK and signed registered agent identities, with the same
  governed query and retrieval services as HTTP.

The RDH integration fixture retains the supplied chunk columns and original
index shapes, without inventing tenant, ACL or approval columns. Its vector
dimension and row count are deliberately reduced for CI. Actual row-level
isolation still requires an approved source-field mapping or governed view; a
UI policy cannot create source ownership data that does not exist.

## Verification and remaining release gates

Local verification: 113 Python tests passed, 17 integration tests skipped;
5 real-API Playwright tests passed (navigation, durable consumer edit, source
onboarding, policy-filtered SQL pagination, overview accessibility/mobile layout).
The production frontend build and JavaScript syntax check passed. Browser data
is explicitly isolated test data; the production app never loads the UI fixture.

CI now includes browser checks and both container builds. Existing PostgreSQL,
MySQL/MariaDB and OpenSearch integration jobs remain enabled. The new RDH physical
schema and deep-cursor EXPLAIN test runs when `EDP_TEST_NATIVE_DSN` is supplied.

Before release, require passing CI, real OIDC login/refresh/logout, native RDH
validation, S3 export download/cancel, MCP consumer interoperability, canary and
rollback exercises, and deployment smoke tests. Local checks did not exercise
these external environments or Docker images. Accessibility coverage is the
overview and navigation, not a full application audit.

The supplied performance assessment is not fully resolved by this change:
production-scale benchmarks, global admission-lock contention, asynchronous
audit ingestion/retention, COPY-based exports and stored-tsvector migration
planning remain separate engineering/release work. No billion-row throughput or
latency claim is made. Telemetry shown in the UI is explicitly bounded to the
serving API replica, not cluster-wide history. Advanced free-form mapping fields
still use JSON inputs; core resource and promotion forms use typed controls.
