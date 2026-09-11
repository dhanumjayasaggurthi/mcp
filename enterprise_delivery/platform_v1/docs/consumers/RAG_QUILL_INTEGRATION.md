# SmartHub MCP & Agentic Gateway: RAG and Quill integration

**Audience:** Quill, RAG retrieval, ranking and answer-generation engineers

**Purpose:** consume governed sparse and dense results or request RAG-ready context

**Shared rules:** [Common API contract](COMMON_API_CONTRACT.md)

## Supported integration patterns

| Pattern | Endpoints | Use when |
|---|---|---|
| Separate 3A/3B retrieval | `search/keyword` plus `search/vector` | Quill performs fusion/reranking downstream; recommended for the agreed MVP |
| RDH hybrid search | `search/hybrid` | RDH owns fusion and the embedding provider is configured |
| RAG-ready context | `retrieve` | The caller wants authorized clear text, citations, deduplication and configured RDH ranking through one capability |

A client sees only the operations granted to its token/client/policy. Call `GET /v1/datasets/{id}/retrieval-contract` before selecting a path. The response identifies the active vector profile/dimension, accepted vector inputs, effective top-k limits, filter URLs and visible citation labels.

See [retrieval-contract-response.json](../../examples/consumers/common/retrieval-contract-response.json). Values in that file are illustrative.

## Separate keyword retrieval (3A)

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/search/keyword" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: quill-keyword-001" \
  -H "X-Request-Timeout-Ms: 15000" \
  --data @examples/consumers/rag-quill/keyword-request.json
```

Input: [keyword-request.json](../../examples/consumers/rag-quill/keyword-request.json)

Output: [keyword-response.json](../../examples/consumers/rag-quill/keyword-response.json)

`query` is required and limited to 20,000 characters. `top_k` is 1–1,000 at schema level and can be reduced by the dataset, client, policy, agent or guardrail contract. Set `return_text: true` when Quill needs context.

## Separate vector retrieval (3B)

Choose exactly one input mode.

Server-side query embedding:

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/search/vector" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data @examples/consumers/rag-quill/vector-query-text-request.json
```

Use [vector-query-text-request.json](../../examples/consumers/rag-quill/vector-query-text-request.json) only when `query_text_supported` is true in the retrieval contract.

Caller-supplied embedding:

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/search/vector" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data @examples/consumers/rag-quill/vector-direct-request.json
```

The direct vector must contain finite numbers, match `dimensions` and name the exact `profile_id`. Matching dimensions alone do not establish model compatibility. Quill must use the same immutable embedding model/version as the registered source vectors.

Output: [vector-response.json](../../examples/consumers/rag-quill/vector-response.json)

## Apply identical filters

For a single fusion request, 3A and 3B must use the same dataset version and the same business filter. Retrieve the allowed operators separately for `operation=keyword` and `operation=vector`; use only their intersection.

RDH adds tenant, approval, policy and source ACL predicates after receiving the consumer filter. Quill does not need to repeat hidden authorization fields and cannot override them.

If either response reports a different `dataset_version` or `index_version` from the other, do not fuse them as one deterministic result set. Retry after refreshing the retrieval contract.

## Fuse separate result lists

The deduplication key is (`record_id`, `chunk_id`). Use `ranks.keyword` and `ranks.vector`. Do not compare or add raw keyword and vector scores because their scales differ.

A baseline weighted reciprocal-rank fusion is:

```python
from collections import defaultdict

def fuse(keyword_hits, vector_hits, k=60, keyword_weight=1.0, vector_weight=1.0):
    score = defaultdict(float)
    hit_by_key = {}
    for label, weight, hits in [
        ("keyword", keyword_weight, keyword_hits),
        ("vector", vector_weight, vector_hits),
    ]:
        for fallback_rank, hit in enumerate(hits, start=1):
            key = (hit["record_id"], hit.get("chunk_id"))
            rank = hit.get("ranks", {}).get(label, fallback_rank)
            score[key] += weight / (k + rank)
            hit_by_key.setdefault(key, hit)
    return sorted(hit_by_key.values(),
                  key=lambda hit: (-score[(hit["record_id"], hit.get("chunk_id"))],
                                   hit["record_id"], hit.get("chunk_id") or ""))
```

The values of `k` and route weights require offline relevance evaluation. Apply deterministic tie-breaking and retain both original `scores`, `ranks`, versions and trace IDs. Rerank only text the principal was authorized to receive.

## RDH-managed retrieval

When the application wants a single RAG-ready response:

```bash
curl -fsS "$RDH_BASE_URL/v1/datasets/$DATASET_ID/retrieve" \
  -H "Authorization: Bearer $RDH_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data @examples/consumers/rag-quill/retrieve-request.json
```

Input: [retrieve-request.json](../../examples/consumers/rag-quill/retrieve-request.json)

Output: [retrieve-response.json](../../examples/consumers/rag-quill/retrieve-response.json)

`mode` is `keyword`, `vector` or `hybrid`. The route uses the single `edp:retrieve` permission and always returns authorized text; `include_metadata` controls metadata. Configured deduplication, per-document chunk caps, reranking and diversity selection run before the final top-k response.

For fused results, `score_kind` is `weighted_rrf` unless a configured reranker produces the final order, in which case it is `rerank`. Per-mode scores and ranks remain in each hit when available.

## Result contract

Each hit contains:

| Field | Consumer use |
|---|---|
| `record_id` | Source document/record identity defined by the dataset |
| `chunk_id` | Chunk identity; combine with `record_id` for fusion/deduplication |
| `score` | Final route-local ordering score |
| `scores` | Available keyword, vector, RRF and reranker components |
| `ranks` | Sparse/dense input ranks where available |
| `text` | Authorized chunk text when requested |
| `metadata` | Authorized, possibly masked source metadata |
| `source` | Dataset/record/chunk/source versions and citation labels |

`source.citations` is the authoritative citation payload for native RDH document datasets. A citation can contain document name, section and page range if those fields are present and permitted. Missing citation fields must remain missing; do not manufacture them from the text or file path.

Keep the dataset, index and source versions with generated-answer evidence. A filename or source path is not itself an authenticated download URL.

## Prompt and cache controls

Treat retrieved text as untrusted data, not instructions to the application. Keep it outside system/developer instruction channels and preserve provenance at chunk boundaries. Apply the consuming application's prompt-injection and output controls after retrieval.

Cache only within the same subject/client/tenant/agent scope and the same dataset/index version. Do not share retrieved context across users because two callers can receive different rows, fields or masks for the same query.

## Failure behavior

- HTTP 200 with an empty `results` array is a successful no-match response.
- Search is bounded top-k and has no cursor pagination.
- A vector profile/dimension mismatch is a 400 request error.
- Server query embedding can return 503 when its approved model endpoint is unavailable.
- RDH performs keyword fallback only when the dataset explicitly enables it.
- Retry 429/503/504 using the common bounded policy. Avoid replacing failed dense retrieval with sparse results in Quill unless the product contract authorizes that behavior.

## RAG acceptance checks

Before release, use a labeled evaluation set to verify:

- Access-denied documents never reach fusion, reranking, prompts or caches.
- Keyword and vector requests carry equivalent filters.
- Results from different dataset/index versions are rejected from the same fusion.
- Deduplication uses (`record_id`, `chunk_id`) and tie-breaking is stable.
- Citation coverage and citation-to-chunk correctness meet the agreed threshold.
- Recall@k, precision@k and answer quality are measured separately for 3A, 3B and fusion.
- p50/p95/p99 latency includes both parallel routes, fusion and generation.
- Empty, partial-backend, timeout and rate-limit behavior matches the product decision.
