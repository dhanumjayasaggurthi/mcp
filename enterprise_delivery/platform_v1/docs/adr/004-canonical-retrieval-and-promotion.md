# ADR 004 — Canonical text, versioned sinks and coordinated promotion

Status: implemented with external search qualification gates.

Canonical text/ACL/provenance is authoritative in `ChunkStore`; the vector index stores vectors and metadata, not a second copy of full text. Keyword indexing necessarily stores searchable text. IDs are deterministic for dataset/schema version, tenant, record, ordinal and unchanged content. Embeddings are batched and cached within model/profile/security scope.

Retrieve candidates through keyword/filtered ANN, fuse, hydrate in batches, and verify committed canonical tenant/record/ACL metadata. Only authorized text reaches optional reranking. Deduplicate, cap chunks per record and optionally diversify the bounded candidates before returning logical citations. This security-first hydration order intentionally precedes semantic reranking.

Workers require all configured sink acknowledgements before SQL completion. Partial sink failures replay safely; orphan external candidates are never enough to authorize content. Promotion commits both deployment rows and one routing pointer so keyword/vector/hydration choose the same version. Quality/canary approval must use actual representative evidence, not fabricated scores.
