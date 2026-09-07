from __future__ import annotations

import pytest

from enterprise_data_platform.backends import (
    DeterministicHashEmbeddingProvider,
    InMemoryCanonicalChunkStore,
    InMemoryKeywordBackend,
    InMemoryStructuredBackend,
    InMemoryVectorBackend,
)
from enterprise_data_platform.catalog import CatalogConflict, InMemoryCatalog
from enterprise_data_platform.cursor import CursorCodec, CursorScopeMismatch, CursorTampered
from enterprise_data_platform.hybrid import rrf_fuse
from enterprise_data_platform.models import (
    AccessPolicy,
    Capability,
    CountMode,
    DataProduct,
    FieldDefinition,
    FieldPolicy,
    PolicyEffect,
    Principal,
    ProductStatus,
    RetrievalHit,
    RetrievalProfile,
    RetrieveRequest,
    SearchRequest,
    SortField,
    SourceBinding,
    StructuredQueryRequest,
    TextProfile,
    VectorProfile,
    VectorSearchRequest,
)
from enterprise_data_platform.policy import PolicyEngine
from enterprise_data_platform.services import AccessDenied, PlatformService


SECRET = b"x" * 32


def product() -> DataProduct:
    p = DataProduct(
        id="regulatory-docs",
        display_name="Regulatory Documents",
        description="Reference test product",
        version="2026.09.06.1",
        status=ProductStatus.ACTIVE,
        source=SourceBinding(
            connector="snowflake",
            environment="prod",
            database="RDH",
            schema_name="CURATED",
            object_name="REGULATORY_DOCS",
        ),
        identity_fields=["id"],
        tenant_field="tenant_id",
        fields=[
            FieldDefinition(name="id", data_type="string", filterable=True, sortable=True, vector_metadata=True),
            FieldDefinition(name="tenant_id", data_type="string", filterable=True, sortable=True, vector_metadata=True),
            FieldDefinition(name="title", data_type="string", filterable=True, sortable=True, keyword_searchable=True, vector_metadata=True),
            FieldDefinition(name="body", data_type="string", keyword_searchable=True),
            FieldDefinition(name="secret", data_type="string", filterable=False, sensitive=True, default_policy=FieldPolicy.MASKED),
        ],
        capabilities={
            Capability.DISCOVER,
            Capability.QUERY,
            Capability.KEYWORD,
            Capability.VECTOR,
            Capability.HYBRID,
            Capability.RETRIEVE,
        },
        default_limit=2,
        max_limit=3,
        max_top_k=5,
        retrieval=RetrievalProfile(
            keyword_index="regulatory-docs-v1",
            text=TextProfile(source_fields=["title", "body"], chunk_size_chars=1000, chunk_overlap_chars=100),
            vector=VectorProfile(
                profile_id="medical-v1",
                embedding_model="test-embedder",
                dimensions=16,
                index_version="idx-001",
                metadata_filter_fields=["tenant_id", "id"],
            ),
        ),
    )
    p.validate_contract()
    return p


def principal(tenant="acme") -> Principal:
    return Principal(subject="svc-regassist", client_id="regassist-prod", tenant=tenant, groups={"rag-consumers"})


def allow_policy() -> AccessPolicy:
    return AccessPolicy(
        id="allow-regassist",
        effect=PolicyEffect.ALLOW,
        dataset_patterns=["regulatory-*"] ,
        operations={Capability.QUERY, Capability.KEYWORD, Capability.VECTOR, Capability.HYBRID, Capability.RETRIEVE},
        client_ids={"regassist-prod"},
        groups={"rag-consumers"},
        allowed_fields={"id", "tenant_id", "title", "body", "secret"},
        max_limit=2,
        max_top_k=3,
        require_tenant_isolation=True,
    )


def build_service():
    p = product()
    catalog = InMemoryCatalog()
    catalog.put(p)
    policy = PolicyEngine([allow_policy()])
    embedder = DeterministicHashEmbeddingProvider()

    rows = [
        {"id": "1", "tenant_id": "acme", "title": "Alpha", "body": "adverse event rules", "secret": "s1"},
        {"id": "2", "tenant_id": "acme", "title": "Alpha", "body": "serious adverse event reporting", "secret": "s2"},
        {"id": "3", "tenant_id": "acme", "title": "Beta", "body": "clinical protocol", "secret": "s3"},
        {"id": "4", "tenant_id": "other", "title": "Alpha", "body": "adverse event other tenant", "secret": "s4"},
    ]
    keyword_hits = [
        RetrievalHit(record_id=r["id"], chunk_id=f"{r['id']}:0", text=r["body"], metadata={"id": r["id"], "tenant_id": r["tenant_id"], "title": r["title"], "secret": r["secret"]})
        for r in rows
    ]
    vector_entries = []
    for hit in keyword_hits:
        vec = embedder.embed(hit.text or "", profile_id="medical-v1", dimensions=16)
        vector_entries.append((hit, vec))
    chunks = {
        "regulatory-docs": {
            f"{r['id']}:0": {
                "text": f"CLEAR TEXT: {r['body']}",
                "metadata": {"id": r["id"], "tenant_id": r["tenant_id"], "title": r["title"], "secret": r["secret"]},
            }
            for r in rows
        }
    }
    return PlatformService(
        catalog=catalog,
        policies=policy,
        cursor_codec=CursorCodec(SECRET, ttl_seconds=3600),
        structured=InMemoryStructuredBackend({"regulatory-docs": rows}),
        keyword=InMemoryKeywordBackend({"regulatory-docs": keyword_hits}),
        vector=InMemoryVectorBackend({"regulatory-docs": vector_entries}),
        embedder=embedder,
        chunks=InMemoryCanonicalChunkStore(chunks),
    ), catalog, policy


def test_cursor_pagination_is_keyset_stable_and_tenant_isolated():
    service, _, _ = build_service()
    req = StructuredQueryRequest(
        select=["id", "title", "secret", "tenant_id"],
        order_by=[SortField(field="title", direction="asc")],
        limit=100,  # policy should cap this to 2
        count_mode=CountMode.EXACT,
    )
    first = service.query(principal(), "regulatory-docs", req)
    assert [r["id"] for r in first.rows] == ["1", "2"]
    assert all(r["tenant_id"] == "acme" for r in first.rows)
    assert all(r["secret"] == "***MASKED***" for r in first.rows)
    assert first.count == 3
    assert first.next_cursor

    second = service.query(
        principal(),
        "regulatory-docs",
        req.model_copy(update={"cursor": first.next_cursor}),
    )
    assert [r["id"] for r in second.rows] == ["3"]
    assert second.next_cursor is None


def test_cursor_tamper_and_cross_dataset_replay_are_rejected():
    codec = CursorCodec(SECRET, ttl_seconds=60)
    token = codec.encode(
        dataset_id="a",
        dataset_version="1",
        position={"id": 10},
        sort=[{"field": "id", "direction": "asc"}],
        now=100,
    )
    with pytest.raises(CursorTampered):
        codec.decode(token[:-1] + ("A" if token[-1] != "A" else "B"), dataset_id="a", dataset_version="1", now=101)
    with pytest.raises(CursorScopeMismatch):
        codec.decode(token, dataset_id="b", dataset_version="1", now=101)


def test_vector_dimension_and_profile_are_validated():
    service, _, _ = build_service()
    with pytest.raises(Exception, match="dimension mismatch"):
        service.vector_search(
            principal(),
            "regulatory-docs",
            VectorSearchRequest(vector=[0.1, 0.2], vector_profile="medical-v1"),
        )


def test_rag_retrieve_returns_clear_text_and_policy_sanitized_metadata():
    service, _, _ = build_service()
    response = service.retrieve(
        principal(),
        "regulatory-docs",
        RetrieveRequest(query="serious adverse event", mode="hybrid", top_k=3),
    )
    assert response.results
    assert all(hit.text and hit.text.startswith("CLEAR TEXT:") for hit in response.results)
    assert all(hit.metadata.get("tenant_id") == "acme" for hit in response.results)
    assert all(hit.metadata.get("secret") == "***MASKED***" for hit in response.results)
    assert all(hit.source["dataset"] == "regulatory-docs" for hit in response.results)


def test_explicit_deny_overrides_allow():
    service, _, policy = build_service()
    policy.put(
        AccessPolicy(
            id="deny-one-client",
            effect=PolicyEffect.DENY,
            dataset_patterns=["regulatory-docs"],
            operations={Capability.QUERY},
            client_ids={"regassist-prod"},
            priority=0,
        )
    )
    with pytest.raises(AccessDenied, match="explicit deny"):
        service.query(principal(), "regulatory-docs", StructuredQueryRequest(select=["id"]))


def test_catalog_optimistic_concurrency_prevents_lost_updates():
    _, catalog, _ = build_service()
    p = catalog.get("regulatory-docs")
    changed = p.model_copy(update={"description": "new", "version": "2"})
    catalog.put(changed, expected_version=p.version)
    stale = p.model_copy(update={"description": "stale", "version": "3"})
    with pytest.raises(CatalogConflict):
        catalog.put(stale, expected_version=p.version)


def test_rrf_deduplicates_and_combines_routes():
    k = [RetrievalHit(record_id="1", chunk_id="1:0", score=100), RetrievalHit(record_id="2", chunk_id="2:0", score=90)]
    v = [RetrievalHit(record_id="2", chunk_id="2:0", score=.99), RetrievalHit(record_id="3", chunk_id="3:0", score=.98)]
    fused = rrf_fuse(k, v, k=60, top_k=3)
    assert fused[0].record_id == "2"
    assert len({(h.record_id, h.chunk_id) for h in fused}) == len(fused)
    assert fused[0].scores["hybrid"] > fused[1].scores["hybrid"]
