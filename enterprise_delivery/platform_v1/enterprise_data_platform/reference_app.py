from __future__ import annotations

import os
from typing import List

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from .api import create_app
from .backends import (
    DeterministicHashEmbeddingProvider,
    InMemoryCanonicalChunkStore,
    InMemoryKeywordBackend,
    InMemoryStructuredBackend,
    InMemoryVectorBackend,
)
from .catalog import InMemoryCatalog
from .control_models import AgentRegistration, ClientRegistration, GuardrailRule, IndexDeployment
from .control_state import ControlState
from .cursor import CursorCodec
from .exports import QueuedExportBackend
from .guardrails import GuardrailEngine
from .models import (
    AccessPolicy,
    Capability,
    DataProduct,
    FieldDefinition,
    PolicyEffect,
    Principal,
    ProductStatus,
    RetrievalHit,
    RetrievalProfile,
    SourceBinding,
    TextProfile,
    VectorProfile,
)
from .policy import PolicyEngine
from .operations import InMemoryOperationsProvider, reference_operations_snapshot
from .services import PlatformService


def _require_reference_mode() -> None:
    if os.getenv("EDP_REFERENCE_MODE", "").lower() not in {"1", "true", "yes"}:
        raise RuntimeError(
            "reference_app is test-only. Set EDP_REFERENCE_MODE=true explicitly. "
            "Production must wire enterprise identity and durable backends."
        )


def _principal(request: Request) -> Principal:
    return Principal(
        subject=request.headers.get("x-subject") or "reference-user",
        client_id=request.headers.get("x-client-id") or "reference-consumer",
        tenant=request.headers.get("x-tenant") or "demo",
        groups=set(filter(None, (request.headers.get("x-groups") or "").split(","))),
    )


def _build_app():
    _require_reference_mode()

    catalog = InMemoryCatalog()
    product = DataProduct(
        id="regulatory-documents",
        display_name="Regulatory Documents",
        description="Reference data product exercising query, keyword, vector, hybrid and RAG retrieval.",
        version="1",
        status=ProductStatus.ACTIVE,
        source=SourceBinding(
            connector="reference-memory",
            environment="dev",
            object_name="REGULATORY_DOCUMENTS",
        ),
        identity_fields=["id"],
        tenant_field="tenant_id",
        fields=[
            FieldDefinition(name="id", data_type="string", filterable=True, sortable=True),
            FieldDefinition(name="tenant_id", data_type="string", filterable=True, sortable=True),
            FieldDefinition(name="title", data_type="string", filterable=True, sortable=True, keyword_searchable=True),
            FieldDefinition(name="body", data_type="string", keyword_searchable=True),
            FieldDefinition(name="document_type", data_type="string", filterable=True, sortable=True, vector_metadata=True),
        ],
        capabilities={
            Capability.DISCOVER,
            Capability.QUERY,
            Capability.KEYWORD,
            Capability.VECTOR,
            Capability.HYBRID,
            Capability.RETRIEVE,
            Capability.EXPORT,
            Capability.MCP,
        },
        default_limit=50,
        max_limit=1000,
        max_top_k=100,
        retrieval=RetrievalProfile(
            keyword_index="reference-keyword-v1",
            vector=VectorProfile(
                profile_id="reference-embedding-v1",
                embedding_model="deterministic-hash-test-only",
                dimensions=16,
                index_version="v1",
                metadata_filter_fields=["tenant_id", "document_type"],
            ),
            text=TextProfile(source_fields=["title", "body"], title_field="title"),
        ),
    )
    product.validate_contract()
    catalog.put(product)

    rows = [
        {
            "id": "DOC-001",
            "tenant_id": "demo",
            "title": "Adverse Event Reporting",
            "body": "Serious adverse events must be assessed, documented, and reported within the applicable regulatory timelines.",
            "document_type": "guideline",
        },
        {
            "id": "DOC-002",
            "tenant_id": "demo",
            "title": "Clinical Protocol Governance",
            "body": "Protocol changes require controlled review, approval, versioning, and traceable implementation across study systems.",
            "document_type": "protocol",
        },
        {
            "id": "DOC-003",
            "tenant_id": "other",
            "title": "Other Tenant Record",
            "body": "This record demonstrates mandatory tenant isolation in the reference application.",
            "document_type": "internal",
        },
    ]

    embedder = DeterministicHashEmbeddingProvider()
    keyword_hits: List[RetrievalHit] = []
    vector_entries = []
    chunks = {product.id: {}}
    for row in rows:
        chunk_id = f"{row['id']}:0"
        hit = RetrievalHit(
            record_id=row["id"],
            chunk_id=chunk_id,
            text=f"{row['title']}\n{row['body']}",
            metadata={
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "title": row["title"],
                "document_type": row["document_type"],
            },
            source={"system": "reference-memory"},
        )
        keyword_hits.append(hit)
        vector_entries.append(
            (
                hit,
                embedder.embed(
                    hit.text or "",
                    profile_id=product.retrieval.vector.profile_id,
                    dimensions=product.retrieval.vector.dimensions,
                ),
            )
        )
        chunks[product.id][chunk_id] = {
            "text": hit.text,
            "metadata": hit.metadata,
        }

    policies = PolicyEngine(
        [
            AccessPolicy(
                id="reference-consumer",
                effect=PolicyEffect.ALLOW,
                dataset_patterns=[product.id],
                operations={
                    Capability.DISCOVER,
                    Capability.QUERY,
                    Capability.KEYWORD,
                    Capability.VECTOR,
                    Capability.HYBRID,
                    Capability.RETRIEVE,
                    Capability.EXPORT,
                    Capability.MCP,
                },
                client_ids={"reference-consumer", "control-hub"},
                allowed_fields={"id", "tenant_id", "title", "body", "document_type"},
                require_tenant_isolation=True,
                max_limit=1000,
                max_top_k=100,
            )
        ]
    )

    control = ControlState()
    control.clients.put(
        ClientRegistration(
            id="reference-consumer",
            display_name="Reference Consumer",
            owner="platform-team",
            auth_mode="workload_identity",
            allowed_datasets={product.id},
            allowed_capabilities={Capability.QUERY, Capability.KEYWORD, Capability.VECTOR, Capability.HYBRID, Capability.RETRIEVE},
            environment="dev",
        )
    )
    control.agents.put(
        AgentRegistration(
            id="reference-agent",
            display_name="Reference MCP Agent",
            owner="platform-team",
            service_principal="reference-agent",
            allowed_datasets={product.id},
            allowed_capabilities={Capability.RETRIEVE, Capability.MCP},
            mcp_enabled=True,
        )
    )
    control.guardrails.put(
        GuardrailRule(
            id="reference-citations",
            name="Require RAG citations",
            scope="global",
            kind="require_citations",
            action="deny",
            severity="high",
        )
    )
    control.indexes.put(
        IndexDeployment(
            id="regulatory-documents-keyword",
            dataset_id=product.id,
            index_type="keyword",
            active_version="v1",
            indexed_records=2,
            shard_count=1,
            replica_count=2,
        )
    )
    control.indexes.put(
        IndexDeployment(
            id="regulatory-documents-vector",
            dataset_id=product.id,
            index_type="vector",
            active_version="v1",
            indexed_records=2,
            shard_count=1,
            replica_count=2,
        )
    )

    queued_exports = []
    exporter = QueuedExportBackend(lambda job_id, payload: queued_exports.append((job_id, payload)))
    guardrails = GuardrailEngine(lambda: control.guardrails.list())
    service = PlatformService(
        catalog=catalog,
        policies=policies,
        cursor_codec=CursorCodec(os.getenv("EDP_CURSOR_SECRET", "reference-only-secret-32-bytes-minimum").encode("utf-8")),
        structured=InMemoryStructuredBackend({product.id: rows}),
        keyword=InMemoryKeywordBackend({product.id: keyword_hits}),
        vector=InMemoryVectorBackend({product.id: vector_entries}),
        embedder=embedder,
        chunks=InMemoryCanonicalChunkStore(chunks),
        exporter=exporter,
        guardrails=guardrails,
    )

    app = create_app(
        service=service,
        catalog=catalog,
        policies=policies,
        control_state=control,
        principal_resolver=_principal,
        control_admin_check=lambda p: "data-platform-admin" in p.groups,
        operations_provider=InMemoryOperationsProvider(reference_operations_snapshot()),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in os.getenv("EDP_CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = _build_app()
