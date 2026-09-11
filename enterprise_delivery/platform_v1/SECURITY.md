# SmartHub MCP & Agentic Gateway production security configuration

## Identity and access

The production factory uses `JWTPrincipalResolver` and requires `EDP_OIDC_ISSUER`, `EDP_OIDC_AUDIENCE` and `EDP_OIDC_JWKS_URL`. JWTs must have an approved signature algorithm, issuer, audience, subject, issued-at and expiry; production does not derive identity from caller-supplied `x-subject`, tenant or group headers.

The issuer owns `client_id`/`azp`, `tenant`, `groups`, optional `agent_id` and OAuth `scope`. A consumer needs an active client registration, a dataset/capability grant and `edp:<operation>` scope. Agent registrations additionally bind a service principal and a narrower set of datasets/capabilities. MCP cannot broaden these grants through tool arguments or retrieved text.

Control administration requires both the `data-platform-admin` group and `edp:admin`. Bootstrap this group at the issuer; never create a public bootstrap endpoint. Production authentication successes, denials, policy decisions, mutations, jobs and promotions are audited without access tokens or request bodies.

Use an API gateway/service mesh for TLS termination and workload mTLS where required. Verify JWTs again in the application. Do not configure a gateway to translate arbitrary user headers into trusted identity. Disable access logs containing query strings at every proxy; the production image disables Uvicorn access logs and emits sanitized request records.

## Secrets and rotation

`EDP_SECRET_DIR` defaults to `/run/secrets/edp`. The production factory uses mounted references such as `file://control-dsn`, `file://cursor-key`, `file://warehouse-dsn`, `file://search-token` and `file://embedding-token`. The mounted-secret provider prevents traversal outside its approved directory. Source registrations persist only references.

Populate mounts through the enterprise secret manager/CSI integration. Vault and cloud secret services can feed these mounts; an AWS Secrets Manager provider boundary is also available for custom composition. The default factory does not resolve arbitrary `env://` or `vault://` references by itself. Kubernetes Secret values and example credentials must not be committed.

Use at least 32 random bytes of cursor-key material, shared by all replicas. Cursors encrypt the signed position and bind identity, policy, query and product version. Rotate the secret and roll API replicas together; old cursors will be invalidated. A multi-key overlap window is not implemented.

Source connection handles are cached by registration revision. On credential rotation, update the secret mount and increment the corresponding source registration revision; the next resolve disposes the old pool. Roll API/workers to rotate cached search/embedding/reranking authorization and control-DSN connections. Configure short issuer JWKS cache lifetimes and test key rotation with the actual identity provider.

## Transport and source grants

PostgreSQL production/control connections use `sslmode=verify-full`; mount the required trust chain. Sources use read-only transactions and must also have a read-only database role. MySQL/MariaDB bindings use PyMySQL with certificate and hostname verification; require secure transport on the server. Snowflake and enterprise dialects require their own verified TLS/driver configuration. Do not set `native_scan_governor=true` until the source-native cost/scan controls have been exercised.

OpenSearch and model endpoints require credential-free HTTPS URLs. REST connector targets are HTTPS/443, explicitly allowlisted, checked against private/special IP ranges and pinned to the resolved address while preserving TLS SNI. Redirects and environment HTTP proxies are disabled. Restrict outbound network paths at the cluster level as a second boundary. Private enterprise REST services should use an approved connector with a verified private egress policy rather than weakening the public REST adapter.

## Encryption and audit

Enable encryption for PostgreSQL storage/backups, OpenSearch storage/snapshots and Kubernetes secret storage. Export uploads require a KMS key, private bucket policy and workload IAM. Deny public ACLs and unrestricted `ListBucket`; grant the worker only its approved export prefix and KMS permissions. Apply `deploy/object-lifecycle.json` and adapt the prefix/retention to enterprise requirements. API download expiry is enforced independently of object deletion.

Run migrations using a separate owner role. The runtime must have DML rights only on required tables, INSERT/SELECT on `edp_audit` and `edp_object_history`, and no ownership/DDL/TRUNCATE privilege. PostgreSQL triggers reject audit/history UPDATE, DELETE and TRUNCATE. They do not stop a database superuser. Stream audit records to a separately administered immutable archive and retain privileged database audit logs.

## Telemetry and incident response

Configure an HTTPS OTLP/HTTP collector with `OTEL_EXPORTER_OTLP_ENDPOINT`. Only method, route templates, status, operation, durations, sizes and non-content counters belong in telemetry. Do not add SQL text, filter values, result rows, source credentials, bearer headers or presigned URLs to spans/logs. High-cardinality tenant/client consumption belongs in restricted audit/usage analytics, not unrestricted metric labels.

On compromise, disable the client/agent or add a deny policy in SQL, stop job intake if necessary, rotate affected references, and inspect immutable audit records. Downloads reauthorize before minting a new URL; an already issued URL remains usable until its short expiry. A failed audit/control-store write fails the request rather than allowing an unaudited cached authorization path.
