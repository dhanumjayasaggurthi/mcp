# SmartHub MCP & Agentic Gateway: structured data integration

**Audience:** reporting services, workflow applications and governed data clients

**Purpose:** query typed fields, resolve records, aggregate approved data and request exports

**Shared rules:** [Common API contract](COMMON_API_CONTRACT.md)

## Endpoint selection

| Need | Endpoint | Scope |
|---|---|---|
| Discover visible datasets | `GET /v1/datasets` | At least one granted data capability |
| Inspect visible fields | `GET /v1/datasets/{id}/schema` | A granted dataset capability |
| Discover filter grammar | `GET /v1/datasets/{id}/filters?operation=query` | `edp:query` |
| Read projected rows | `POST /v1/datasets/{id}/query` | `edp:query` |
| Resolve one or more IDs | `POST /v1/datasets/{id}/records/lookup` | `edp:query` |
| Aggregate at the source | `POST /v1/datasets/{id}/aggregate` | `edp:aggregate` |
| Submit a governed export | `POST /v1/datasets/{id}/exports` | `edp:export` |

A count measure and `count_mode: exact` also require `edp:exact_count` plus matching client, dataset and policy grants.

## Query multiple fields

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/query" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: reporting-query-001" \
  -H "X-Request-Timeout-Ms: 15000" \
  --data @examples/consumers/structured/query-request.json
```

Input: [query-request.json](../../examples/consumers/structured/query-request.json)

Output: [query-response.json](../../examples/consumers/structured/query-response.json)

The example combines document ID, page range, array overlap, nested title/level conditions and a negated filename condition in one request. All values are bound parameters and filters run at the source when supported.

`select` contains logical fields from the caller-visible schema. An empty `select` returns the caller's permitted selectable fields. Hidden fields are rejected; masked fields are returned masked and cannot be used for filtering or ordering.

## Cursor pagination

Offset pagination is not supported. The service appends unique identity fields to the requested sort to create deterministic keyset pages.

For every next page, repeat the same `select`, `filter`, `order_by` and dataset, then add:

```json
{
  "cursor": "<next_cursor from the prior response>"
}
```

The actual cursor belongs inside the complete original request body. Stop when `next_cursor` is null and `has_more` is false. Treat the cursor as opaque and sensitive because it represents an authorized query position. Do not decode, edit or share it across identities.

A changed dataset version, identity, policy, filter, projection or sort invalidates the cursor. Restart pagination from the beginning after a cursor-scope error.

## Counts

`count_mode` supports:

| Value | Behavior |
|---|---|
| `none` | Default; no count work |
| `estimate` | Planner estimate when the backend supports it; `count_is_estimate` is true |
| `exact` | Exact source count under the separate exact-count authorization |

Use `none` for normal paging. An estimate can be absent when the source has no safe estimator. Exact counts can be costly and may be rejected by source scan budgets even when authorized.

## Exact ID lookup

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/records/lookup" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data @examples/consumers/structured/lookup-request.json
```

See [lookup-request.json](../../examples/consumers/structured/lookup-request.json).

The request accepts 1–100 string or integer IDs and a page limit up to 1,000. Send `id_field` unless the published contract has one unambiguous identity/default record field. For a composite identity, use `query` with an AND predicate for all key components.

A lookup may also include a filter, projection, sort and cursor. RDH ANDs the ID predicate with that filter and all mandatory authorization predicates. Missing or unauthorized IDs produce no row and are not distinguished.

## Aggregation

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/aggregate" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data @examples/consumers/structured/aggregate-request.json
```

Input: [aggregate-request.json](../../examples/consumers/structured/aggregate-request.json)

Output: [aggregate-response.json](../../examples/consumers/structured/aggregate-response.json)

An aggregate request supports up to 16 group fields and 16 measures. Measure functions are `count`, `sum`, `avg`, `min` and `max`. `sum`/`avg` require numeric fields. Results are source-pushed and bounded to 1–1,000 groups.

Aggregation responses use the structured response envelope. `has_more: true` means the bounded group result was truncated; aggregate pagination is not available in v1. Narrow the filters or group dimensions rather than assuming the returned groups are complete.

## Exports

Use exports only when the dataset grants `export` and the environment has an export worker/object store.

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/exports" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: reporting-run-2026-09-11-001" \
  --data @examples/consumers/structured/export-request.json
```

Input: [export-request.json](../../examples/consumers/structured/export-request.json)

Initial HTTP 202 output: [export-response.json](../../examples/consumers/structured/export-response.json)

Poll `GET /v1/exports/{job_id}`. Cancel with `DELETE /v1/exports/{job_id}`. After success, call `GET /v1/exports/{job_id}/download` to receive a short-lived download URL. These routes exist only when the export backend is configured.

Use one stable `Idempotency-Key` for the same logical submission. Reusing it with a different payload causes a conflict. Download authorization is rechecked and the URL must not be logged or shared. Exports are bounded by row/byte/retention policy and do not guarantee one cross-page source snapshot.

## Performance rules

- Project only needed fields.
- Put selective indexed equality/range predicates in the request.
- Prefer ID lookup for known identities.
- Use cursor pagination instead of requesting the maximum page size.
- Avoid exact counts in interactive flows.
- Use asynchronous export for approved bulk extraction.
- Treat 429 as admission control; honor `Retry-After` instead of increasing concurrency.
- Ask RDH to review an actual query/filter workload before adding source indexes.

The API has source timeouts, scan estimates where supported, bounded pools and result-size limits. These controls prevent unbounded work; they do not guarantee that every syntactically valid broad query will execute.

## Structured consumer acceptance checks

Before production traffic, verify:

- The discovered schema contains only approved fields.
- Every intended multi-field predicate appears in filter discovery.
- NULL, no-match and masked-field behavior matches the application.
- Cursor pages contain no gaps or duplicates for an unchanged source.
- Cross-tenant and unauthorized IDs return no data.
- Exact count and count aggregation are denied without the separate grant.
- Broad scans are rejected within the application's timeout.
- Export idempotency, cancellation, expiry and download reauthorization work in the target object store.
