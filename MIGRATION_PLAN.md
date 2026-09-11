# SmartHub MCP & Agentic Gateway migration plan

## 1. Establish a recovery point

Keep the existing reference/legacy deployment available during migration. Export its Data Product, policy, client, agent, guardrail and index registrations through the existing administrative interfaces. Preserve source IDs and alias mappings. Do not copy production credentials into JSON configuration. Take an environment backup and verify a restore before introducing the production control store.

The new schema is additive and separate from the legacy SQLite metadata tables. The initial migration does not transform an existing enterprise database in place. `migrations/README.md` describes creation, permissions and rollback boundaries.

## 2. Provision and register

1. Provision an external HA PostgreSQL control database, an OIDC audience, gateway/mesh TLS, secret mounts, and restricted network egress.
2. Run `python -m enterprise_data_platform.workers migrate` once using DDL credentials. API/worker credentials must not own the schema or audit tables.
3. Start the production factory with the control DSN and cursor key references. Configure search/embedding and object storage only for enabled retrieval/export workloads.
4. Bootstrap registrations using an OIDC principal in `data-platform-admin` with `edp:admin`. The admin path permits bootstrap before consumer registrations exist.
5. Register source configurations by secret reference. Validate the source's TLS, driver, scan governor and timeout behavior before enabling its products.
6. Import Data Products and policies. A changed product requires a new semantic version. Policy/client/agent/guardrail/index updates must carry the current integer `revision`; deletes require `expected_revision`. Data Product updates/deletes require `expected_version`.
7. Register consumer identities and scope them to specific datasets and capabilities. Map legacy aliases with `AliasRegistration(id="api.alias", api_id="api", alias="alias", dataset_id="product")`.

## 3. Compatibility changes

| Consumer behavior | New behavior | Migration action |
|---|---|---|
| Bearer header | Preserved | Continue `Authorization: Bearer ...`; credentials never enter generated URLs |
| Legacy API-key principal | Not accepted as production OIDC identity | Exchange credentials at an approved gateway or onboard an OAuth/workload client |
| `/data-api/{api_id}/tables` | Returns authorized logical aliases | Populate durable alias registrations |
| `/{alias}/columns` | Governed logical schema | Hidden fields and physical source names are excluded |
| `/{alias}/rows` | Same rows/count keys plus cursor metadata | Prefer `cursor`; omit `include_total` |
| Legacy offsets | New alias facade: max 1,000 rows and 20 keyset hops; original patched handler: max offset 10,000 | Move deep scans to v1 cursors or exports |
| Exact count | Separate `exact_count` product/policy/client/OAuth/agent permission | Add only for approved use cases; no automatic count |
| Aggregate | Separate `aggregate` capability; count measures also require `exact_count` | Use `/v1/datasets/{id}/aggregate` |
| Policy changed while paging | Existing cursor is rejected | Restart from page one under the new grant |
| Token refreshed with different identity attributes | Scope-bound cursor may be rejected | Restart; do not edit the cursor |
| Search over hidden/masked text or identity fields | Denied | Define a safe retrieval product with explicitly authorized text and citation identity |
| CSV/JSONL compression | `none` or `gzip` | Set explicitly; the Parquet default is `zstd` |
| Mutable UI registrations | Optimistic concurrency enforced | Reload conflicts and review current state before retrying |

All exact-count access restrictions are intentional security changes. Existing policy-only tests were updated to grant the new capability explicitly; row-query behavior remains covered by legacy and v1 tests.

## 4. Cursor example

```http
POST /v1/datasets/customer-orders/query
Authorization: Bearer <OIDC access token>
Content-Type: application/json

{"select":["id","amount"],"limit":100,"count_mode":"none"}
```

Use the returned `next_cursor` with the same projection, filter, order and identity. `has_more` and `returned_rows` describe the page. Both sort directions use NULLS LAST; identity columns are appended to ensure deterministic compound ordering. Internal ordering values remain encrypted in production cursors.

Pagination is not a long-lived database snapshot. Concurrent source updates can move rows across the ordering boundary. For repeatable exports, register an immutable source snapshot/materialized version or use a connector with a verified snapshot contract. The default exporter does not manufacture cross-page snapshot isolation.

## 5. Retrieval and CDC cutover

Provision a new keyword/vector version before publishing events. Capture a source-native snapshot/CDC boundary, publish bounded pages with resume tokens, and run independent indexing workers. Do not acknowledge a source page before `publish_page` commits. Validate tombstones, duplicate delivery, ordering and partial sink failures for the actual source.

Backfill canonical chunks and candidate indexes, then run the repository's quality evidence gate plus a representative judged retrieval set. Validate cross-tenant negative cases on active and candidate versions. Move traffic through the coordinated canary/promotion API. Keep the old version until rollback and retention gates pass.

## 6. Export cutover

Enable private object storage with KMS encryption, workload IAM, multipart cleanup and the supplied lifecycle policy. Start export workers separately. Submit with an `Idempotency-Key`, poll `/v1/exports/{id}`, and request `/download` only after success. Downloads recheck current identity and policy, expire within five minutes, and cannot extend the artifact's retention.

Exports reauthorize each page. An expired identity, changed dataset/policy, cancelled job or exhausted quota stops the export. Use an approved renewable workload delegation design for exports longer than the submitting token's validity; this implementation fails closed instead of extending identity grants.

## 7. Control Hub authentication and rollback

Build without `VITE_REFERENCE_MODE`. The enterprise OIDC host must call `configureAuthentication(async () => session.accessToken)` from `frontend/apiClient.js`, or provide `globalThis.edpAuth.getAccessToken`. Tokens remain in memory and headers. The library does not implement an IdP-specific sign-in page.

Shift gateway traffic gradually after identity, policy, source and tenant parity checks. On failure, stop new job intake and restore the previous API routing while workers drain or leases expire. Roll index traffic back with the coordinated controller. Do not route new production JWT requests into the header-trusting reference app. Do not drop durable job, audit or canonical tables as an application rollback; preserve them for recovery and investigation.
