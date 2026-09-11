# Common consumer API contract

**Contract:** REST API v1

**Media type:** `application/json`

**Authentication:** OAuth 2.0 bearer access token

**Machine contract:** `GET /openapi.json`

**Interactive renderers:** `/docs` and `/redoc` when exposed by the environment gateway

Use the base URL issued by RDH. Do not store access tokens in source code, request bodies, query strings or logs.

## Authorization

Every data request needs all of the following:

1. A valid bearer token for the configured issuer and audience.
2. A registered `client_id`/`azp`.
3. The OAuth scope for the operation, such as `edp:query` or `edp:keyword`.
4. A client registration that grants the dataset and capability.
5. A matching RDH access policy.

Tenant, group, subject and optional agent claims come from the signed token. Consumer-supplied tenant or group headers are not trusted in production. Dataset, tenant, row, field and ACL restrictions are applied by RDH and cannot be broadened with a request filter.

## Request headers

```http
Authorization: Bearer <access-token>
Accept: application/json
Content-Type: application/json
X-Request-ID: <1-64 letters, digits, or hyphens>
X-Request-Timeout-Ms: 15000
traceparent: <W3C trace context, when available>
```

`X-Request-ID` is optional. RDH accepts it only when it matches the bounded format; otherwise it creates an identifier. The response returns `X-Request-ID` and `X-Trace-ID`. The maximum accepted timeout is 30 seconds. A deployment may apply a shorter deadline.

Use `Idempotency-Key` for export submission. Query, lookup and search operations are read-only.

## Discovery before execution

A consumer should discover its effective contract after obtaining a token:

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN"

curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/capabilities" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN"

curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/schema" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN"

curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/filters?operation=query" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN"
```

RAG clients also call:

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/retrieval-contract" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN"
```

Discovery is caller-specific: inaccessible datasets, fields, operations and citation labels are omitted. Cache it only for a bounded time within the same identity/security scope. Refresh it when a response reports a new `dataset_version` or an administrator announces a contract or policy change.

An illustrative response is in [retrieval-contract-response.json](../../examples/consumers/common/retrieval-contract-response.json). The live response is authoritative.

## Filter grammar

A filter is one leaf or a nested Boolean expression.

```json
{
  "and": [
    {"field": "doc_id", "op": "in", "value": ["DOC-001", "DOC-002"]},
    {"field": "page_start", "op": "between", "value": [1, 20]},
    {
      "or": [
        {"field": "chunk_level", "op": "eq", "value": "section"},
        {"field": "section_title", "op": "contains", "value": "safety"}
      ]
    },
    {"not": {"field": "file_name", "op": "ends_with", "value": ".tmp"}}
  ]
}
```

The filter-discovery response lists the exact operators permitted for each field and operation. The complete engine vocabulary is:

| Field family | Operators |
|---|---|
| Scalar | `eq`, `neq`, `in`, `not_in`, `exists` |
| Ordered number/date/time/text | `gt`, `gte`, `lt`, `lte`, `between` when advertised |
| Text | `contains`, `starts_with`, `ends_with` |
| PostgreSQL array | `array_contains_all`, `array_overlaps`, `array_is_empty` |
| PostgreSQL JSONB | `json_contains`; scalar path predicates with `"path": ["site", "country"]` |
| Boolean | Nested `and`, `or`, `not` |

`between` is inclusive. Date and timestamp values use ISO 8601. Text matching is case-insensitive literal matching for structured/native filters. NULL follows SQL three-valued logic: only TRUE returns a row. Use `exists` or `eq: null` when NULL is intentional.

Limits are 64 KiB per filter, depth 12, 256 expression nodes, 1,000 values in a list and eight JSON path elements. The request body limit is 1 MB. Field types and values are validated before source execution. Always use discovery because a backend can support a smaller operator set.

## Responses and versions

Successful structured responses include `trace_id`, `returned_rows`, `has_more` and an opaque `next_cursor`. Retrieval responses include `dataset_id`, `dataset_version`, `index_version`, `score_kind`, `results` and `trace_id`.

Treat these identifiers separately:

- `dataset_version` identifies the published data-product schema and retrieval configuration.
- `index_version` identifies the active retrieval index or source embedding version.
- `source_version` on a hit identifies the underlying source row/version when configured.
- `trace_id` is the support and audit correlation key.

Clients must ignore unknown response fields to allow additive v1 changes. They must not infer business approval, EDMS identity or document access from field names. Use only the registered dataset contract.

## Pagination

Structured query and exact lookup use opaque keyset cursors. Repeat the original dataset, projection, filter and sort with the returned cursor. Stop when `next_cursor` is null. A cursor is bound to dataset version, identity, policy and query scope; restart from the first page after a scope/version error.

Search and retrieval are bounded top-k operations and do not paginate. A search request containing a cursor is rejected.

## Errors

Errors use this shape:

```json
{
  "detail": "request validation failed",
  "code": "422",
  "trace_id": "01JEXAMPLETRACE00000000000000"
}
```

See [error-response.json](../../examples/consumers/common/error-response.json).

| HTTP | Meaning | Consumer action |
|---|---|---|
| 400 | Invalid filter, cursor, vector profile or request semantics | Correct the request; do not retry unchanged |
| 401 | Missing, expired or invalid identity | Obtain a valid token |
| 403 | Client, scope, policy, field or row access denied | Request the required grant; do not change filters to bypass it |
| 404 | Dataset/resource unavailable to this contract | Verify environment and dataset ID |
| 409 | Version/idempotency conflict | Read current state before resubmitting the state-changing request |
| 413 | Request exceeds the body budget | Reduce it |
| 422 | JSON does not match the request schema | Correct the payload |
| 429 | Capacity/rate limit reached | Honor `Retry-After`, then retry with jitter |
| 503 | Required source/search/model service unavailable | Retry a bounded number of times with exponential backoff |
| 504 | Deadline exceeded | Retry read-only calls with backoff or narrow the request |

Record the HTTP status, `trace_id`, dataset ID and client-side timestamp for support. Do not log bearer tokens, query text, filter values, returned document text or presigned download URLs.

For 429/503/504, use a bounded retry policy such as three attempts with full jitter and an overall deadline. Do not retry 4xx responses other than 429 without changing the cause. Use a fresh OAuth token when the existing token may expire during retries.

## Data handling

Responses already reflect row authorization, tenant constraints, field grants, masks and source ACL checks. Empty results can mean no match or no visible matching row; the API does not reveal which. Preserve the response's classification and handling requirements in caches, prompts and logs. Cache only within the same subject/client/tenant security scope and dataset/index version.
