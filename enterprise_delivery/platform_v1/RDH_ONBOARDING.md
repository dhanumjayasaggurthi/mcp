# RDH / DataHub: deploy, onboard and serve

This release adds direct PostgreSQL retrieval over the supplied chunk-table layout, governed exact-ID lookup, source inspection and typed compound filters. Existing keyword and vector APIs remain separate: RegAssist/Quill can combine their independently ranked results. Native PostgreSQL datasets do not need an OpenSearch copy or an indexing worker.

This is deployable application code, not evidence that an RDH production environment has been deployed or capacity-qualified. Supply environment configuration, authoritative access rules and the actual embedding profile before enabling consumers.

## What RDH provides

| Team question | Implemented response |
|---|---|
| 3A sparse retrieval | `POST /v1/datasets/{id}/search/keyword`; PostgreSQL full-text matching with `ts_rank_cd` ranking, or the existing OpenSearch backend. |
| 3B dense retrieval | `POST /v1/datasets/{id}/search/vector`; native pgvector HNSW with pushed-down filters. A registered embedding profile is mandatory. |
| Separate scores/ranks | Hits contain `scores.keyword` / `scores.vector` and corresponding `ranks`; responses identify dataset/config versions and score kind. Scores from the two routes have different scales. |
| Known document IDs (2a) | `POST /v1/datasets/{id}/records/lookup`, `id_field: "doc_id"`. Returns authorized chunks with keyset pagination; does not invoke embeddings. `doc_id` is not automatically an EDMS number. |
| Q/HAQ IDs (2b) | Register the actual Q table as a structured dataset and look up its verified identity. No Q vector database is required. The source Q/HAQ-to-EDMS mapping was not supplied. |
| Metadata and citations | Registered source fields, including file name, section, page range, document ID and chunk ID. Citation labels map to actual columns; field permissions/masking apply. |
| Filters | Typed nested `and`, `or`, `not` over multiple fields on structured/native keyword/native vector requests. Discovery shows the caller's allowed fields and operations. |
| Index ownership | RDH/source DBA owns source indexes and statistics. API generates reviewable online DDL and validates bindings; its source role only reads data. |
| Approval/access | Dataset `mandatory_filter`, tenant scope, policy row predicates and optional source ACL arrays constrain queries, exact lookup and retrieval. Approval information must come from an authoritative business source. |
| CDC | Native retrieval reads committed source rows directly. Q-to-RDH replication still needs the source-specific publisher/capture integration; the existing durable publication API accepts those events. |

API paths above use the shipped route names; query examples are in [examples/rdh](examples/rdh).

## 1. Prepare the runtime

Follow [DEPLOYMENT.md](DEPLOYMENT.md) for image build, control-database migrations, runtime grants, OIDC, secrets, ingress and network policy. Use PostgreSQL 16 and pgvector 0.8+ for the tested native path. The control database and source database are separate logical responsibilities; configure separate credentials even if sharing infrastructure.

Use the supplied native topology as the base for your environment overlay:

```bash
kubectl kustomize deploy/kubernetes/postgres-native > /tmp/rdh-native.yaml
```

It keeps the API and its availability resources and removes indexing/export workers. Replace the image with your reviewed registry digest, complete OIDC/control settings and dependency egress, and mount `edp-runtime-secrets`. Migrate using the separate migration job/DDL role before starting the API. The base's `.invalid` dependencies are placeholders, not deployable environment values.

Required secret files include `control-dsn`, `cursor-key`, and `rdh-source-dsn`; files must be readable by runtime UID 10001. Use a PostgreSQL DSN with verified TLS, for example a driver URL beginning `postgresql+psycopg://` with `sslmode=verify-full` and the trusted CA configured. Keep passwords and tokens out of JSON examples and git.

The source role needs CONNECT, schema USAGE and SELECT on the curated source objects, plus metadata visibility. It must not be able to alter the corpus or create indexes. Run generated index plans separately as the source owner. Account for source pool size multiplied by API replicas.

`/livez` checks process liveness; `/readyz` checks control-state availability. A successful consumer request is needed to verify each source. Readiness alone does not validate all datasets.

## 2. Register and inspect the PostgreSQL source

An admin access token must carry the configured issuer/audience, `data-platform-admin` group and `edp:admin` scope. The CLI reads the token from a protected file and uses HTTPS without redirecting credentials.

```bash
python -m enterprise_data_platform.admin_cli \
  --base-url https://YOUR_RDH_API --token-file /secure/admin-token \
  register-source --file examples/rdh/source-registration.json
```

For existing registrations, retrieve the current revision and put that revision in the update body. Register one source per independently managed database/credential boundary; datasets choose schema and table within the source.

Admin inspection endpoints:

- `GET /v1/control/sources/rdh-postgres/objects?schema_name=rimdocs_extracts_core&limit=100`
- `POST /v1/control/sources/rdh-postgres/inspect` with `{"schema_name":"rimdocs_extracts_core","object_name":"doc_chunks_clinical"}`

Inspection reads columns, declared types, identity and indexes. It does not expose sample rows. Object discovery is bounded and reports truncation. Automatic drafting requires a primary key. Explicit contracts can use a verified, non-null unique key. For a governed join without an enforceable unique identity, provide a keyed curated table/materialized view.

## 3. Draft the dataset and provision indexes

```bash
python -m enterprise_data_platform.admin_cli \
  --base-url https://YOUR_RDH_API --token-file /secure/admin-token \
  preview --file examples/rdh/clinical-preview.json --output clinical-draft.json

python -m enterprise_data_platform.admin_cli \
  --base-url https://YOUR_RDH_API --token-file /secure/admin-token \
  index-plan --file clinical-draft.json --output clinical-index-plan.json
```

The preview creates an unsaved draft. Review selectable/filterable fields, hide internal paths, set the actual tenant field if present, and configure the authoritative approval constraint. Do not grant all raw rows merely because inspection succeeded. The screenshots show ingestion state, not document approval, tenant or ACL columns.

Source mappings for the provided layouts:

| Source shape | Template and identity |
|---|---|
| RimDocs `doc_chunks_*`, EPOD `doc_chunks` / `doc_chunks_ind` | `postgres_chunks`; identity `chunk_id`, record ID `doc_id`, text `chunk_text`, vector `chunk_vector`. |
| `ingestion_log_*` | `table`; identity `doc_id`; typed status/date/size filters. This is processing metadata. |
| `ai_assistant.messages` | `table`; identity `id`; JSONB `content` and `tool_calls`; chat access needs an authoritative row policy. |
| `epod.file_data` | `table`; composite identity `folder_path`, `file_name`; use `/query` with both predicates. |
| `epod.folder_progress` | `table`; identity `folder_path`. |

For another source, change source/schema/object/dataset ID and rerun inspection. SQL structured capabilities remain connector-specific; native PostgreSQL retrieval is only enabled for PostgreSQL. Registration never assumes another source has the identical schema.

The generated plan adds:

1. GIN on the configured `to_tsvector` expression (or existing tsvector column).
2. B-tree on document ID, chunk order and chunk ID for ordered document lookup.
3. HNSW when a vector profile is supplied.
4. `ANALYZE` to refresh planner statistics.

The clinical trigram GIN shown in the screenshot does not replace the full-text GIN needed for ranked keyword search. Review equivalent existing indexes and invalid concurrent builds before running each statement outside a transaction. Index builds on the supplied multi-GB tables consume substantial I/O and disk; schedule them using actual database capacity.

## 4. Enable vector retrieval using the actual model

The initial preview intentionally enables keyword retrieval first. The screenshots declare `public.vector` without a dimension. They do not identify the embedding model. Add the **actual** `vector_profile` to the preview input and regenerate the draft:

```json
{
  "profile_id": "YOUR_IMMUTABLE_PROFILE_ID",
  "embedding_model": "YOUR_IMMUTABLE_MODEL_VERSION",
  "dimensions": 1536,
  "distance": "cosine",
  "index_version": "YOUR_SOURCE_EMBEDDING_VERSION"
}
```

1536 is an illustrative value, not a finding about your data. Replace it with the verified dimension. For an unbounded `vector` source column, set `postgres.cast_vector: true`; the index plan and query will use the same dimension-specific cast. Validate every existing embedding before building that expression index. Native `vector` HNSW supports at most 2000 dimensions in this implementation. Mixed models need separately governed datasets/indexes, even if their dimensions match.

Clients may provide a numeric `vector` plus its registered `vector_profile`. To accept `query_text`, configure the approved embedding endpoint, token reference and `EDP_EMBEDDING_PROFILES` mapping documented in [DEPLOYMENT.md](DEPLOYMENT.md). Source and query embeddings must use the same immutable model. Native retrieval does not generate or update stored source embeddings.

Cosine scores are `1 - distance`; L2 and inner-product routes return negative distance. ANN is approximate: selective filters can yield fewer than `top_k` within the configured scan budget. pgvector 0.8 iterative scans improve filtered recall; tune `hnsw_ef_search` and `hnsw_max_scan_tuples` against latency and labeled relevance data.

## 5. Validate, save, activate and grant consumer access

```bash
python -m enterprise_data_platform.admin_cli \
  --base-url https://YOUR_RDH_API --token-file /secure/admin-token \
  validate --file clinical-draft.json --output clinical-validation.json

python -m enterprise_data_platform.admin_cli \
  --base-url https://YOUR_RDH_API --token-file /secure/admin-token \
  save-dataset --file clinical-draft.json
```

Validation reports issues rather than executing DDL. Resolve them before activation. Save the draft, then change its version to a new immutable version (for example `2`) and status to `active`. Save that file with `--expected-version 1`. Activation revalidates the physical binding, key, types, full-text index and vector index/dimension configuration. Preserve revision/version checks on later updates.

Register the actual OAuth client ID through `PUT /v1/control/clients/{id}` using `client-registration.json`. Replace its owner. Register `read-policy.json` at `PUT /v1/control/policies/{id}` after replacing the authorized group and reviewing its allowed fields. The token's client/group claims must match. Client registration alone is not a data grant. Keep vector grants disabled until its profile is configured.

For private conversations and documents, apply the actual tenant/owner/approval conditions before consumer testing. If ACL arrays exist, map them with `acl_subjects_field` and/or `acl_groups_field`; configured subject and group constraints both apply. Null/empty arrays mean unrestricted by that ACL dimension, so confirm this matches the source's semantics.

## 6. Consumer filtering and pagination

`GET /v1/datasets/{id}/filters?operation=query` returns the caller's usable filter fields and operators. Use `operation=keyword` or `vector` for the search contract. An unsupported field/operator is rejected, not silently ignored.

| Field type | Main operators |
|---|---|
| Scalar | `eq`, `neq`, `in`, `not_in`, `exists` |
| Ordered number/date/text | `gt`, `gte`, `lt`, `lte`, `between` where advertised |
| Text | `contains`, `starts_with`, `ends_with` (case-insensitive literal matching) |
| PostgreSQL array | `array_contains_all`, `array_overlaps`, `array_is_empty` |
| PostgreSQL JSONB | `json_contains`; scalar path predicates using `path: ["site", "country"]` |
| Boolean expressions | Nested `and`, `or`, `not` across different fields |

See `query-request.json` for document IDs + page range + content types + nested section/name predicates in one request. `between` is inclusive. `exists: false` matches absent/null values; use it explicitly when nulls should qualify. Ordinary SQL comparisons against NULL do not match, including negated comparisons. JSON key structure comes from the source contract, not an assumed message format.

Filters are bounded to 64 KiB, depth 12 and 256 nodes; list predicates accept at most 1000 values. Values are typed and bound to SQL parameters. Consumer filters are ANDed with dataset, tenant, policy and ACL restrictions. Hidden/masked fields cannot be used to infer protected values through filter discovery.

- `/query` and `/records/lookup` return `next_cursor`; send it with the same filter/projection/order. Cursor scope binds identity, policy and dataset version.
- Exact lookup accepts at most 100 IDs, returning a bounded page of chunks. Continue until no cursor remains to assemble a document.
- Keyword/vector search returns bounded top-k results. Search cursors are rejected; it is not a corpus-export API.
- Separate keyword/vector results can be fused by RegAssist/Quill using record/chunk IDs. The platform's optional hybrid route remains available where enabled.
- Citation values are available only when present and permitted. A raw file path is not an authenticated download URL. Source-document approval/access and business citation presentation still require their authoritative integration.

## Validation and release gates

CI uses an isolated PostgreSQL/pgvector service and a 10,000-row fixture modeled on the supplied chunks. Tests cover compound array/JSON filters, source ACL/tenant/approval exclusion, ID pagination, injection resistance, citations, immediate source deletion and planner use of full-text/HNSW indexes. Other CI jobs exercise MySQL, MariaDB, OpenSearch, frontend and deployment packaging.

These checks establish functional behavior and index-compatible SQL. They do not establish performance on 30-GB tables, 100M/1B rows or your peak concurrency. Before admitting production load, measure p50/p95/p99, error/429 rates, recall@k, scanned rows, buffer reads, pool saturation and update/index lag on representative sources and filters. Include selective, broad, no-match and denied-access cases. Add composite/GIN/expression indexes for the actual filter workload and refresh statistics. Never remove row authorization to achieve an SLO.

Required environment inputs remain: runtime/control/source credentials and TLS, approved image/ingress/network policy, source access and business approval rules, verified embedding model/dimension, actual Q/HAQ-to-EDMS mappings, and measured capacity/HA acceptance. No source data or production infrastructure is changed by the onboarding preview or index-plan APIs.
