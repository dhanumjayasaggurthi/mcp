from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Capability(str, Enum):
    DISCOVER = "discover"
    QUERY = "query"
    AGGREGATE = "aggregate"
    EXACT_COUNT = "exact_count"
    KEYWORD = "keyword"
    VECTOR = "vector"
    HYBRID = "hybrid"
    RETRIEVE = "retrieve"
    EXPORT = "export"
    MCP = "mcp"


class ProductStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class CountMode(str, Enum):
    NONE = "none"
    ESTIMATE = "estimate"
    EXACT = "exact"


class FieldPolicy(str, Enum):
    VISIBLE = "visible"
    MASKED = "masked"
    HIDDEN = "hidden"


class FieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=128)
    nullable: bool = True
    selectable: bool = True
    filterable: bool = False
    sortable: bool = False
    keyword_searchable: bool = False
    vector_metadata: bool = False
    sensitive: bool = False
    default_policy: FieldPolicy = FieldPolicy.VISIBLE


class SourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: str = Field(min_length=1, max_length=128)
    source_id: Optional[str] = Field(default=None, max_length=128)
    environment: str = Field(min_length=1, max_length=64)
    database: Optional[str] = None
    schema_name: Optional[str] = None
    object_name: str = Field(min_length=1, max_length=512)
    partition_hints: List[str] = Field(default_factory=list)


class TextProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_fields: List[str] = Field(min_length=1)
    title_field: Optional[str] = None
    template: Optional[str] = None
    chunk_size_chars: int = Field(default=3000, ge=256, le=20000)
    chunk_overlap_chars: int = Field(default=300, ge=0, le=5000)

    @field_validator("chunk_overlap_chars")
    @classmethod
    def overlap_reasonable(cls, value: int, info):
        chunk = info.data.get("chunk_size_chars", 3000)
        if value >= chunk:
            raise ValueError("chunk_overlap_chars must be smaller than chunk_size_chars")
        return value


class VectorProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=128)
    embedding_model: str = Field(min_length=1, max_length=256)
    dimensions: int = Field(ge=8, le=65536)
    distance: Literal["cosine", "dot", "l2"] = "cosine"
    index_version: str = Field(min_length=1, max_length=128)
    metadata_filter_fields: List[str] = Field(default_factory=list)


class PostgresRetrievalProfile(BaseModel):
    """Administrator-owned mapping onto an existing, pre-chunked source table."""
    model_config = ConfigDict(extra="forbid")

    chunk_id_field: str = "chunk_id"
    record_id_field: str = "doc_id"
    text_field: str = "chunk_text"
    vector_field: Optional[str] = "chunk_vector"
    vector_schema: str = Field(default="public", pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    cast_vector: bool = False
    source_version_field: Optional[str] = "updated_at"
    keyword_tsvector_field: Optional[str] = None
    keyword_mode: Literal['full_text', 'contains'] = 'full_text'
    text_search_config: str = Field(default="pg_catalog.english", pattern=r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
    acl_subjects_field: Optional[str] = None
    acl_groups_field: Optional[str] = None
    hnsw_ef_search: int = Field(default=100, ge=1, le=1000)
    hnsw_max_scan_tuples: int = Field(default=20000, ge=100, le=1000000)
    iterative_scan: bool = True
    citation_fields: Dict[str, str] = Field(default_factory=dict, max_length=16)


class RetrievalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["opensearch", "postgres"] = "opensearch"
    postgres: Optional[PostgresRetrievalProfile] = None
    keyword_index: Optional[str] = None
    vector: Optional[VectorProfile] = None
    text: Optional[TextProfile] = None
    hybrid_rrf_k: int = Field(default=60, ge=1, le=1000)
    hybrid_keyword_weight: float = Field(default=1.0, gt=0, le=10)
    hybrid_vector_weight: float = Field(default=1.0, gt=0, le=10)
    rerank_profile: Optional[str] = None
    deduplicate_content: bool = True
    max_chunks_per_record: int = Field(default=3, ge=1, le=100)
    diversity_lambda: float = Field(default=1, ge=0, le=1)
    allow_keyword_fallback: bool = False


class DataProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    display_name: str = Field(min_length=1, max_length=256)
    description: str = ""
    version: str = Field(min_length=1, max_length=64)
    status: ProductStatus = ProductStatus.DRAFT
    source: SourceBinding
    identity_fields: List[str] = Field(min_length=1)
    fields: List[FieldDefinition] = Field(min_length=1)
    capabilities: Set[Capability] = Field(default_factory=lambda: {Capability.DISCOVER})
    default_limit: int = Field(default=100, ge=1, le=10000)
    max_limit: int = Field(default=1000, ge=1, le=10000)
    max_top_k: int = Field(default=100, ge=1, le=1000)
    retrieval: Optional[RetrievalProfile] = None
    tenant_field: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    mandatory_filter: Optional[Dict[str, Any]] = None

    @field_validator("fields")
    @classmethod
    def unique_fields(cls, values: List[FieldDefinition]):
        names = [f.name for f in values]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        return values

    def field_map(self) -> Dict[str, FieldDefinition]:
        return {f.name: f for f in self.fields}

    def validate_contract(self) -> None:
        fmap = self.field_map()
        if len(self.fields) > 512 or len(self.identity_fields) > 16:
            raise ValueError("dataset exceeds field or identity budget")
        if self.retrieval and self.retrieval.backend == "postgres":
            mapping = self.retrieval.postgres
            if not mapping or not self.retrieval.text:
                raise ValueError("PostgreSQL retrieval requires postgres and text mappings")
            required = [mapping.chunk_id_field, mapping.record_id_field, mapping.text_field]
            optional = [mapping.source_version_field, mapping.keyword_tsvector_field,
                        mapping.acl_subjects_field, mapping.acl_groups_field]
            if self.retrieval.vector:
                required.append(mapping.vector_field)
                if self.retrieval.vector.dimensions > 2000:
                    raise ValueError("native vector HNSW supports at most 2000 dimensions; use a qualified halfvec adapter for larger vectors")
            required += [x for x in optional if x] + list(mapping.citation_fields.values())
            if any(x not in fmap for x in required):
                raise ValueError("PostgreSQL retrieval mapping references missing fields")
            if self.identity_fields != [mapping.chunk_id_field]:
                raise ValueError("native chunk retrieval requires a unique chunk identity")
            if self.retrieval.text.source_fields != [mapping.text_field]:
                raise ValueError("native retrieval uses existing chunk text without re-chunking")
        from .query_validation import validate_filter
        from .principal_filters import bind_filter
        validate_filter(self, bind_filter(self.mandatory_filter, validate_only=True))

        missing_identity = [f for f in self.identity_fields if f not in fmap]
        if missing_identity:
            raise ValueError(f"identity fields missing from schema: {missing_identity}")
        if self.tenant_field and self.tenant_field not in fmap:
            raise ValueError(f"tenant_field '{self.tenant_field}' is not in schema")
        if self.default_limit > self.max_limit:
            raise ValueError("default_limit cannot exceed max_limit")
        if self.retrieval and self.retrieval.text:
            missing = [f for f in self.retrieval.text.source_fields if f not in fmap]
            if missing:
                raise ValueError(f"text profile fields missing from schema: {missing}")
        if Capability.VECTOR in self.capabilities:
            if not self.retrieval or not self.retrieval.vector:
                raise ValueError("vector capability requires retrieval.vector")
        if Capability.RETRIEVE in self.capabilities:
            if not self.retrieval or not self.retrieval.text:
                raise ValueError("retrieve capability requires retrieval.text")


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=512)
    client_id: Optional[str] = Field(default=None, max_length=256)
    tenant: Optional[str] = Field(default=None, max_length=256)
    groups: Set[str] = Field(default_factory=set)
    attributes: Dict[str, str] = Field(default_factory=dict)
    agent_id: Optional[str] = Field(default=None, max_length=128)


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class AccessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,128}$")
    enabled: bool = True
    effect: PolicyEffect
    dataset_patterns: List[str] = Field(default_factory=lambda: ["*"])
    operations: Set[Capability] = Field(default_factory=set)
    subjects: Set[str] = Field(default_factory=set)
    client_ids: Set[str] = Field(default_factory=set)
    groups: Set[str] = Field(default_factory=set)
    tenants: Set[str] = Field(default_factory=set)
    allowed_fields: Optional[Set[str]] = None
    denied_fields: Set[str] = Field(default_factory=set)
    mandatory_filter: Optional[Dict[str, Any]] = None
    max_limit: Optional[int] = Field(default=None, ge=1, le=10000)
    max_top_k: Optional[int] = Field(default=None, ge=1, le=1000)
    require_tenant_isolation: bool = False
    priority: int = Field(default=100, ge=0, le=10000)
    revision: int = Field(default=0, ge=0)


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str
    matched_policy_ids: List[str] = Field(default_factory=list)
    allowed_fields: Set[str] = Field(default_factory=set)
    masked_fields: Set[str] = Field(default_factory=set)
    mandatory_filter: Optional[Dict[str, Any]] = None
    max_limit: int = 0
    max_top_k: int = 0


class SortField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    direction: Literal["asc", "desc"] = "asc"


class StructuredQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    select: List[str] = Field(default_factory=list)
    filter: Optional[Dict[str, Any]] = None
    order_by: List[SortField] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10000)
    cursor: Optional[str] = None
    count_mode: CountMode = CountMode.NONE


class StructuredQueryResponse(BaseModel):
    rows: List[Dict[str, Any]]
    next_cursor: Optional[str] = None
    count: Optional[int] = None
    count_is_estimate: bool = False
    trace_id: str
    has_more: bool = False
    returned_rows: int = 0


class ErrorResponse(BaseModel):
    detail: str
    code: str
    trace_id: str


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: List[str | int] = Field(min_length=1, max_length=100)
    id_field: Optional[str] = None
    select: List[str] = Field(default_factory=list)
    filter: Optional[Dict[str, Any]] = None
    order_by: List[SortField] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)
    cursor: Optional[str] = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=20000)
    filter: Optional[Dict[str, Any]] = None
    top_k: int = Field(default=20, ge=1, le=1000)
    cursor: Optional[str] = None
    return_text: bool = False
    return_metadata: bool = True


class VectorSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: Optional[str] = Field(default=None, max_length=20000)
    vector: Optional[List[float]] = None
    vector_profile: Optional[str] = None
    filter: Optional[Dict[str, Any]] = None
    top_k: int = Field(default=20, ge=1, le=1000)
    return_text: bool = False
    return_metadata: bool = True

    @field_validator("vector")
    @classmethod
    def vector_finite(cls, value: Optional[List[float]]):
        if value is None:
            return value
        if not value:
            raise ValueError("vector cannot be empty")
        import math
        if any(not math.isfinite(float(x)) for x in value):
            raise ValueError("vector values must be finite")
        return value

    def validate_one_input(self) -> None:
        if bool(self.query_text) == bool(self.vector):
            raise ValueError("provide exactly one of query_text or vector")


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=20000)
    mode: Literal["auto", "keyword", "vector", "hybrid"] = "auto"
    filter: Optional[Dict[str, Any]] = None
    top_k: int = Field(default=10, ge=1, le=1000)
    include_metadata: bool = True


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="allow")

    record_id: str
    chunk_id: Optional[str] = None
    score: float = 0.0
    text: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source: Dict[str, Any] = Field(default_factory=dict)
    scores: Dict[str, float] = Field(default_factory=dict)
    ranks: Dict[str, int] = Field(default_factory=dict)
    canonical: Optional[Dict[str, Any]] = Field(default=None, exclude=True)


class RetrievalResponse(BaseModel):
    dataset_id: Optional[str] = None
    dataset_version: Optional[str] = None
    index_version: Optional[str] = None
    score_kind: Optional[str] = None
    results: List[RetrievalHit]
    trace_id: str
    next_cursor: Optional[str] = None


class RetrievalOperationContract(BaseModel):
    endpoint: str
    oauth_scope: str
    max_top_k: int = Field(ge=1, le=1000)
    filters_url: str
    citation_labels: List[str] = Field(default_factory=list)


class RetrievalVectorContract(BaseModel):
    profile_id: str
    dimensions: int = Field(ge=1)
    distance: Literal["cosine", "dot", "l2"]
    accepted_inputs: List[Literal["vector", "query_text"]]
    query_text_supported: bool


class RetrievalScoreContract(BaseModel):
    route_local: bool = True
    comparable_across_modes: bool = False
    score_kind_reported_per_response: bool = True
    ranks_reported_per_mode: bool = True


class RetrievalContractResponse(BaseModel):
    dataset_id: str
    dataset_version: str
    index_version: str
    operations: Dict[str, RetrievalOperationContract]
    vector: Optional[RetrievalVectorContract] = None
    result_identity: List[str]
    pagination: Literal["bounded_top_k"]
    scores: RetrievalScoreContract


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    select: List[str] = Field(default_factory=list)
    filter: Optional[Dict[str, Any]] = None
    format: Literal["parquet", "csv", "jsonl"] = "parquet"
    compression: Optional[Literal["gzip", "zstd", "snappy", "none"]] = "zstd"


class ExportStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExportJob(BaseModel):
    id: str
    dataset_id: str
    requested_by: str
    status: ExportStatus
    created_at: str
    updated_at: str
    format: str
    row_count: Optional[int] = None
    bytes_written: Optional[int] = None
    download_url: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


class ServiceHealth(BaseModel):
    name: str
    status: Literal["healthy", "degraded", "down"]
    details: Dict[str, Any] = Field(default_factory=dict)
