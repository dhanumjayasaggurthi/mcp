from __future__ import annotations

import uuid
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

from .backends import (
    CanonicalChunkStore,
    EmbeddingProvider,
    ExportBackend,
    KeywordBackend,
    StructuredBackend,
    VectorBackend,
    eval_filter,
)
from .catalog import CatalogStore
from .cursor import CursorCodec, CursorError
from .hybrid import rrf_fuse
from .guardrails import GuardrailEngine
from .models import (
    Capability,
    CountMode,
    ExportJob,
    ExportRequest,
    Principal,
    RetrievalHit,
    RetrievalResponse,
    RetrieveRequest,
    SearchRequest,
    StructuredQueryRequest,
    StructuredQueryResponse,
    VectorSearchRequest,
)
from .policy import PolicyEngine, and_filters
from .query_validation import QueryValidationError, referenced_filter_fields, stable_order, validate_filter, validate_projection
from .context import trace_id
from .durable import fingerprint


class AccessDenied(PermissionError):
    pass


class CapabilityUnavailable(RuntimeError):
    pass


class PlatformService:
    def __init__(
        self,
        *,
        catalog: CatalogStore,
        policies: PolicyEngine,
        cursor_codec: CursorCodec,
        structured: Optional[StructuredBackend] = None,
        keyword: Optional[KeywordBackend] = None,
        vector: Optional[VectorBackend] = None,
        embedder: Optional[EmbeddingProvider] = None,
        chunks: Optional[CanonicalChunkStore] = None,
        exporter: Optional[ExportBackend] = None,
        guardrails: Optional[GuardrailEngine] = None,
        retrieval_pipeline=None,
    ) -> None:
        self.catalog = catalog
        self.policies = policies
        self.cursor_codec = cursor_codec
        self.structured = structured
        self.keyword = keyword
        self.vector = vector
        self.embedder = embedder
        self.chunks = chunks
        self.exporter = exporter
        self.guardrails = guardrails
        from .retrieval import RetrievalPipeline
        self.retrieval_pipeline = retrieval_pipeline or RetrievalPipeline()

    def _decision(self, principal: Principal, dataset_id: str, operation: Capability):
        product = self.catalog.get(dataset_id)
        decision = self.policies.evaluate(principal=principal, product=product, operation=operation)
        if not decision.allowed:
            raise AccessDenied(decision.reason)
        return product, decision

    @staticmethod
    def _mask_row(row: Dict[str, Any], masked_fields: set[str]) -> Dict[str, Any]:
        if not masked_fields:
            return row
        out = dict(row)
        for field in masked_fields:
            if field in out and out[field] is not None:
                out[field] = "***MASKED***"
        return out

    @staticmethod
    def _validate_user_fields(request, decision):
        fields = referenced_filter_fields(request.filter)
        fields |= {s.field for s in getattr(request, 'order_by', [])}
        if fields - (decision.allowed_fields - decision.masked_fields):
            raise AccessDenied('filter or ordering references restricted fields')

    @staticmethod
    def _text_allowed(product, decision):
        profile = product.retrieval.text if product.retrieval else None
        return bool(profile and (set(profile.source_fields) | set(product.identity_fields)) <= (decision.allowed_fields - decision.masked_fields))

    def query(self, principal: Principal, dataset_id: str, request: StructuredQueryRequest) -> StructuredQueryResponse:
        return self._query_as(principal, dataset_id, request, Capability.QUERY)

    def aggregate(self, principal, dataset_id, request):
        from .aggregation import aggregate
        return aggregate(self, principal, dataset_id, request)

    def export_page(self, principal, dataset_id, request):
        if request.count_mode != CountMode.NONE:
            raise AccessDenied('export scans cannot request counts')
        return self._query_as(principal, dataset_id, request, Capability.EXPORT)

    def _query_as(self, principal, dataset_id, request, operation):
        if self.structured is None:
            raise CapabilityUnavailable("structured backend is not configured")
        product, decision = self._decision(principal, dataset_id, operation)
        if request.count_mode == CountMode.EXACT:
            _, count_decision = self._decision(principal, dataset_id, Capability.EXACT_COUNT)
            decision.mandatory_filter = and_filters(decision.mandatory_filter, count_decision.mandatory_filter)
        self._validate_user_fields(request, decision)
        fields = validate_projection(product, request.select, decision.allowed_fields)
        combined_filter = and_filters(request.filter, decision.mandatory_filter)
        validate_filter(product, combined_filter)
        order = stable_order(product, request.order_by)
        sort_payload = [item.model_dump() for item in order]
        scope = fingerprint({'principal': principal.model_dump(),
            'decision': decision.model_dump(), 'operation': operation.value, 'filter': combined_filter, 'fields': fields, 'sort': sort_payload})

        position = None
        if request.cursor:
            payload = self.cursor_codec.decode(
                request.cursor,
                dataset_id=product.id,
                dataset_version=product.version,
            )
            if payload.get("sort") != sort_payload:
                raise CursorError("cursor sort order does not match this request")
            if payload.get('extra', {}).get('scope') != scope:
                raise CursorError('cursor security or query scope changed; restart pagination')
            position = payload.get("position")
            if set(position) != {s.field for s in order}:
                raise CursorError('cursor position does not match ordering')

        effective_limit = min(request.limit, decision.max_limit)
        if self.guardrails is not None:
            gd = self.guardrails.evaluate(
                principal=principal, product=product, operation=operation,
                requested_fields=fields, limit=effective_limit,
            )
            if gd.max_limit is not None:
                effective_limit = min(effective_limit, gd.max_limit)
        backend_fields = list(fields) + [item.field for item in order if item.field not in fields]
        page = self.structured.query(
            product=product,
            fields=backend_fields,
            filter_expr=combined_filter,
            order_by=order,
            limit=effective_limit,
            position=position,
            count_mode=request.count_mode,
        )
        rows = [
            self._mask_row({field: row.get(field) for field in fields}, decision.masked_fields)
            for row in page.rows
        ]
        next_cursor = None
        if page.next_position:
            next_cursor = self.cursor_codec.encode(
                dataset_id=product.id,
                dataset_version=product.version,
                position=page.next_position,
                sort=sort_payload,
                extra={'scope': scope},
            )
        return StructuredQueryResponse(
            rows=rows,
            next_cursor=next_cursor,
            count=page.count,
            count_is_estimate=page.count_is_estimate,
            trace_id=trace_id(),
            has_more=next_cursor is not None,
            returned_rows=len(rows),
        )

    def keyword_search(self, principal: Principal, dataset_id: str, request: SearchRequest) -> RetrievalResponse:
        if self.keyword is None:
            raise CapabilityUnavailable("keyword backend is not configured")
        product, decision = self._decision(principal, dataset_id, Capability.KEYWORD)
        self._validate_user_fields(request, decision)
        if not self._text_allowed(product, decision):
            raise AccessDenied('search text sources are restricted')
        combined_filter = and_filters(request.filter, decision.mandatory_filter)
        validate_filter(product, combined_filter)
        top_k = min(request.top_k, decision.max_top_k)
        if self.guardrails is not None:
            gd = self.guardrails.evaluate(principal=principal, product=product, operation=Capability.KEYWORD, top_k=top_k)
            if gd.max_top_k is not None:
                top_k = min(top_k, gd.max_top_k)
        hits = self.keyword.search(
            product=product,
            query=request.query,
            filter_expr=combined_filter,
            top_k=top_k,
        )
        hits = self._finalize_hits(principal, product, decision, hits, combined_filter,
            query=getattr(request, 'query', None) or getattr(request, 'query_text', None), top_k=top_k,
            return_text=request.return_text, return_metadata=request.return_metadata)
        return RetrievalResponse(results=hits, trace_id=trace_id())

    def vector_search(self, principal: Principal, dataset_id: str, request: VectorSearchRequest) -> RetrievalResponse:
        if self.vector is None:
            raise CapabilityUnavailable("vector backend is not configured")
        request.validate_one_input()
        product, decision = self._decision(principal, dataset_id, Capability.VECTOR)
        self._validate_user_fields(request, decision)
        if not self._text_allowed(product, decision):
            raise AccessDenied('search text sources are restricted')
        if not product.retrieval or not product.retrieval.vector:
            raise CapabilityUnavailable("dataset has no vector profile")
        profile = product.retrieval.vector
        combined_filter = and_filters(request.filter, decision.mandatory_filter)
        validate_filter(product, combined_filter)

        if request.vector is not None:
            if len(request.vector) != profile.dimensions:
                raise QueryValidationError(
                    f"vector dimension mismatch: expected {profile.dimensions}, got {len(request.vector)}"
                )
            if request.vector_profile and request.vector_profile != profile.profile_id:
                raise QueryValidationError("vector_profile does not match the dataset's active profile")
            vector = request.vector
        else:
            if self.embedder is None:
                raise CapabilityUnavailable("query-text embedding provider is not configured")
            vector = self.embedder.embed(
                request.query_text or "",
                profile_id=profile.profile_id,
                dimensions=profile.dimensions,
            )

        top_k = min(request.top_k, decision.max_top_k)
        if self.guardrails is not None:
            gd = self.guardrails.evaluate(principal=principal, product=product, operation=Capability.VECTOR, top_k=top_k)
            if gd.max_top_k is not None:
                top_k = min(top_k, gd.max_top_k)
        hits = self.vector.search(
            product=product,
            vector=vector,
            filter_expr=combined_filter,
            top_k=top_k,
        )
        hits = self._finalize_hits(principal, product, decision, hits, combined_filter,
            query=getattr(request, 'query', None) or getattr(request, 'query_text', None), top_k=top_k,
            return_text=request.return_text, return_metadata=request.return_metadata)
        return RetrievalResponse(results=hits, trace_id=trace_id())

    def hybrid_search(self, principal: Principal, dataset_id: str, request: SearchRequest) -> RetrievalResponse:
        if self.keyword is None or self.vector is None or self.embedder is None:
            raise CapabilityUnavailable("hybrid search requires keyword, vector and embedding backends")
        product, decision = self._decision(principal, dataset_id, Capability.HYBRID)
        self._validate_user_fields(request, decision)
        if not self._text_allowed(product, decision):
            raise AccessDenied('search text sources are restricted')
        if not product.retrieval or not product.retrieval.vector:
            raise CapabilityUnavailable("dataset has no vector profile")
        combined_filter = and_filters(request.filter, decision.mandatory_filter)
        validate_filter(product, combined_filter)
        top_k = min(request.top_k, decision.max_top_k)
        if self.guardrails is not None:
            gd = self.guardrails.evaluate(principal=principal, product=product, operation=Capability.HYBRID, top_k=top_k)
            if gd.max_top_k is not None:
                top_k = min(top_k, gd.max_top_k)
        candidate_k = min(max(top_k * 4, 50), decision.max_top_k)
        keyword_hits = self.keyword.search(
            product=product,
            query=request.query,
            filter_expr=combined_filter,
            top_k=candidate_k,
        )
        profile = product.retrieval.vector
        from .resilience import BackendUnavailable
        try:
            query_vector = self.embedder.embed(request.query, profile_id=profile.profile_id, dimensions=profile.dimensions)
            vector_hits = self.vector.search(product=product, vector=query_vector, filter_expr=combined_filter, top_k=candidate_k)
        except BackendUnavailable:
            if not product.retrieval.allow_keyword_fallback:
                raise
            vector_hits = []
        fused = rrf_fuse(
            keyword_hits,
            vector_hits,
            k=product.retrieval.hybrid_rrf_k,
            keyword_weight=product.retrieval.hybrid_keyword_weight,
            vector_weight=product.retrieval.hybrid_vector_weight,
            top_k=top_k,
        )
        fused = self._finalize_hits(principal, product, decision, fused, combined_filter,
            query=request.query, top_k=top_k, return_text=request.return_text, return_metadata=request.return_metadata)
        return RetrievalResponse(results=fused, trace_id=trace_id())

    def retrieve(self, principal: Principal, dataset_id: str, request: RetrieveRequest) -> RetrievalResponse:
        if self.chunks is None:
            raise CapabilityUnavailable("canonical chunk store is not configured")
        product, decision = self._decision(principal, dataset_id, Capability.RETRIEVE)
        self._validate_user_fields(request, decision)
        if not self._text_allowed(product, decision):
            raise AccessDenied('canonical text sources are restricted')
        # Retrieve uses the dedicated RETRIEVE policy gate, then calls the
        # underlying retrieval primitives directly so clients do not need extra
        # permissions merely because a product's configured RAG mode is hybrid.
        combined_filter = and_filters(request.filter, decision.mandatory_filter)
        validate_filter(product, combined_filter)
        top_k = min(request.top_k, decision.max_top_k)
        require_citations = False
        if self.guardrails is not None:
            gd = self.guardrails.evaluate(principal=principal, product=product, operation=Capability.RETRIEVE, top_k=top_k)
            if gd.max_top_k is not None:
                top_k = min(top_k, gd.max_top_k)
            require_citations = gd.require_citations

        if request.mode == "keyword":
            if self.keyword is None:
                raise CapabilityUnavailable("keyword backend is not configured")
            hits = self.keyword.search(product=product, query=request.query, filter_expr=combined_filter, top_k=top_k)
        elif request.mode == "vector":
            hits = self._vector_hits_for_retrieve(product, request.query, combined_filter, top_k)
        else:
            if self.keyword is None:
                raise CapabilityUnavailable("keyword backend is not configured")
            keyword_hits = self.keyword.search(product=product, query=request.query, filter_expr=combined_filter, top_k=min(max(top_k * 4, 50), decision.max_top_k))
            from .resilience import BackendUnavailable
            try:
                vector_hits = self._vector_hits_for_retrieve(product, request.query, combined_filter, min(max(top_k * 4, 50), decision.max_top_k))
            except BackendUnavailable:
                if not product.retrieval.allow_keyword_fallback:
                    raise
                vector_hits = []
            retrieval = product.retrieval
            assert retrieval is not None
            hits = rrf_fuse(
                keyword_hits,
                vector_hits,
                k=retrieval.hybrid_rrf_k,
                keyword_weight=retrieval.hybrid_keyword_weight,
                vector_weight=retrieval.hybrid_vector_weight,
                top_k=top_k,
            )

        results = self._finalize_hits(principal, product, decision, hits, combined_filter,
            query=request.query, top_k=top_k, return_text=True, return_metadata=request.include_metadata)
        return RetrievalResponse(results=results, trace_id=trace_id())

    def _finalize_hits(self, principal, product, decision, hits, filter_expr, *, query, top_k, return_text, return_metadata):
        if self.chunks is None:
            raise CapabilityUnavailable('canonical security verification is not configured')
        ids = [h.chunk_id for h in hits if h.chunk_id]
        hydrated = self.chunks.get_chunks(product=product, chunk_ids=ids)
        clean_hits = []
        for hit in hits:
            chunk = hydrated.get(hit.chunk_id)
            if not chunk or not chunk.get('text') or not eval_filter(chunk.get('metadata') or {}, filter_expr):
                continue
            if chunk.get('record_id', hit.record_id) != hit.record_id or chunk.get('dataset_version', product.version) != product.version:
                continue
            acl = chunk.get('acl') or {}
            if acl.get('subjects') and principal.subject not in acl['subjects']:
                continue
            if acl.get('groups') and not principal.groups.intersection(acl['groups']):
                continue
            clean_hits.append(RetrievalHit(record_id=hit.record_id, chunk_id=hit.chunk_id,
                score=hit.score, scores=hit.scores, text=str(chunk['text']),
                metadata=self._sanitize_metadata(chunk.get('metadata') or {}, decision.allowed_fields, decision.masked_fields),
                source={'dataset': product.id, 'dataset_version': product.version, 'record_id': hit.record_id,
                    'chunk_id': hit.chunk_id, 'source_version': chunk.get('source_version')}))
        # Canonical ACL check occurs before text reaches the optional reranker.
        results = self.retrieval_pipeline.finish(clean_hits, query=query, profile=product.retrieval, top_k=top_k)
        for hit in results:
            if not return_text:
                hit.text = None
            if not return_metadata:
                hit.metadata = {}
        return results

    def export(self, principal: Principal, dataset_id: str, request: ExportRequest) -> ExportJob:
        if self.exporter is None:
            raise CapabilityUnavailable("async export backend is not configured")
        product, decision = self._decision(principal, dataset_id, Capability.EXPORT)
        self._validate_user_fields(request, decision)
        fields = validate_projection(product, request.select, decision.allowed_fields)
        combined_filter = and_filters(request.filter, decision.mandatory_filter)
        validate_filter(product, combined_filter)
        if self.guardrails is not None:
            self.guardrails.evaluate(
                principal=principal, product=product, operation=Capability.EXPORT, requested_fields=fields,
            )
        if hasattr(self.exporter, 'submit_governed'):
            return self.exporter.submit_governed(product=product, principal=principal, request=request,
                fields=fields, decision=decision, filter_expr=combined_filter)
        if decision.masked_fields.intersection(fields):
            raise AccessDenied('export backend cannot enforce field masking')
        return self.exporter.submit(
            product=product,
            principal_subject=principal.subject,
            fields=fields,
            filter_expr=combined_filter,
            format=request.format,
            compression=request.compression,
        )

    def _vector_hits_for_retrieve(self, product, query: str, filter_expr, top_k: int):
        if self.vector is None or self.embedder is None or not product.retrieval or not product.retrieval.vector:
            raise CapabilityUnavailable("vector retrieval is not configured")
        profile = product.retrieval.vector
        vector = self.embedder.embed(query, profile_id=profile.profile_id, dimensions=profile.dimensions)
        return self.vector.search(product=product, vector=vector, filter_expr=filter_expr, top_k=top_k)

    @classmethod
    def _sanitize_metadata(cls, metadata: Dict[str, Any], allowed_fields: set[str], masked_fields: set[str]) -> Dict[str, Any]:
        return cls._mask_row({k: v for k, v in metadata.items() if k in allowed_fields}, masked_fields)

    @classmethod
    def _sanitize_hit(cls, hit: RetrievalHit, allowed_fields: set[str], masked_fields: set[str], return_text: bool) -> RetrievalHit:
        out = hit.model_copy(deep=True)
        out.metadata = cls._sanitize_metadata(out.metadata, allowed_fields, masked_fields)
        if not return_text:
            out.text = None
        return out
