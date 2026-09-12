# SmartHub MCP & Agentic Gateway — Control workspace

This console operates the gateway's authenticated APIs. Resource counts come from durable registrations; requests in the explorer execute under the signed-in identity. Empty, unavailable and denied states remain visible. Reference fixtures are available only through an explicit reference build.

## Run locally

Start the API on port 8080, then run `npm ci && npm run dev` in this directory. Vite proxies API calls to `EDP_API_UPSTREAM` (default `http://127.0.0.1:8080`). Production builds use `npm run check && npm run build`. No production credentials are embedded at build time.

## Deploy

Build this directory's Dockerfile and publish the image to your registry. It serves static assets and proxies `/v1/`, readiness, liveness and OpenAPI to `API_UPSTREAM`. The default upstream is `http://edp-api:8080`; set it to your API service address. Terminate HTTPS at your ingress. Route the console host to the console service on port 8080.

The independent `../deploy/kubernetes/console` Kustomize package provides two replicas, a ClusterIP service, a public runtime ConfigMap, resource limits and a non-root container with a read-only root filesystem. Replace the image with your published immutable release, configure the upstream in the same namespace, edit runtime configuration, and attach your site's ingress and network policy before applying. `/console-health` probes static delivery only; the Environment screen separately checks the API control store.

The API still requires its documented PostgreSQL migration, identity configuration, mounted secrets and source registrations. This console does not provision those dependencies or execute source DDL.

## Authentication

Production uses gateway-validated bearer tokens. Administrators need the `data-platform-admin` group and `edp:admin` scope. Consumer access also needs client registrations, operation scopes and matching policies. Being a console administrator does not grant record access.

Mount a public `runtime-config.js` at `/usr/share/nginx/html/runtime-config.js`:
```js
window.smarthubConfig = {
  apiBaseUrl: "",
  auth: {
    clientId: "your-public-console-client-id",
    authorizationEndpoint: "https://identity.example.com/oauth2/authorize",
    tokenEndpoint: "https://identity.example.com/oauth2/token",
    redirectUri: "https://smarthub.example.com/",
    scope: "openid profile edp:admin edp:query edp:keyword edp:vector edp:retrieve"
  },
  environments: [
    { label: "QA workspace", url: "https://smarthub-qa.example.com/" }
  ]
};
```
Register an OAuth public client supporting authorization-code PKCE/S256 and the exact redirect URI. Its token endpoint must allow browser CORS from the console origin; its access token must match the gateway issuer/audience. Never include a client secret. No iframe or implicit OAuth flow is used.

Alternatively the host can provide `window.edpAuth.getAccessToken()`, optional `signIn()` and `signOut()` before startup. An issued access token can also be entered on the sign-in screen. Access and refresh tokens stay in memory, not localStorage or sessionStorage. Only the short-lived PKCE verifier/state transaction crosses the redirect in sessionStorage. Reloading requires a host session, SSO or another token. Sign-out clears local credentials; configure the host hook for identity-provider logout when needed.

Use a same-origin API proxy. A separate HTTPS `apiBaseUrl` requires an explicitly configured CORS gateway. Configured environment links open separate workspaces and do not pass credentials in the URL. The image's CSP permits HTTPS identity-token requests and forbids framing; tighten connect-src to your exact identity host at the ingress when your deployment requires it.

`VITE_REFERENCE_MODE=true` is only for a deliberately isolated reference runtime; never set it for production builds. Reference mode does not synthesize operational metrics.

## Available workflows

| Screen | Actual operation |
| --- | --- |
| Overview | Durable registration counts, worker queue, configuration audit |
| Sources | Register by secret reference, check connectivity, inspect SQL objects |
| Data products | Inspect schema, generate a draft, review fields, validate binding, save immutable versions |
| Policies / consumers / agents / guardrails | Guided configuration, advanced JSON, optimistic saves, explicit deletion |
| Search indexes | Register versions, submit measured evaluation evidence, gated canary/promotion/rollback |
| API explorer | Caller-specific discovery, query, ID lookup, keyword/vector/hybrid/retrieve and aggregate where permitted |
| Audit | Time-bounded exact filters, stable keyset pages, export the displayed page |
| Monitoring | Bounded observations from the responding replica and shared durable job counts |
| MCP tools | Actual facade tool definitions and agent registrations; this does not establish a deployed MCP transport |
| Environment / My access | Actual runtime, readiness, identity, groups and scopes |

Registration lists request at most 100 records per page and expose Load more. Search and sorting apply to loaded registrations. The API caps a control page at 200; audit caps at 100. Query results use the original request with the returned cursor. The quick filter builder combines up to 20 rules with AND or OR; the request JSON supports the server's full nested AND/OR/NOT contract and operation-specific typed operators.

All API calls have a 35-second client deadline and an 8 MiB response budget. The server's stricter resource budgets remain authoritative. Writes are never automatically retried. On a timeout, refresh before repeating a mutation. On a 409, reopen the resource to load its current revision.

## Verification and operational scope

CI builds and checks the React app, tests console API authorization/pagination/audit/telemetry, starts a real SQLite-backed governed service for Playwright workflows, and builds/starts the non-root read-only console image. Browser tests do not mock API responses. Their isolated records and tokens exist only in the test composition.

The browser evidence artifact contains desktop/mobile screenshots and failure traces. These checks do not certify your live identity provider, source permissions, ingress, HA failover or production traffic volume. Qualify those in your deployment.

Monitoring retains at most 10,000 observations from the latest five minutes per API process. Empty windows show unavailable latency/error percentages. It reports truncation, restart scope and exporter configuration explicitly; use your OpenTelemetry backend for fleet-wide history and SLOs.
