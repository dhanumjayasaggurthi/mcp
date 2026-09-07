from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from .models import Capability


class ManagedStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DEGRADED = "degraded"


class ClientRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,128}$")
    display_name: str
    owner: str
    status: ManagedStatus = ManagedStatus.ACTIVE
    auth_mode: Literal["oauth2", "workload_identity", "mtls", "legacy_api_key"] = "oauth2"
    allowed_datasets: Set[str] = Field(default_factory=set)
    allowed_capabilities: Set[Capability] = Field(default_factory=set)
    rate_limit_rps: int = Field(default=50, ge=1, le=100000)
    max_concurrency: int = Field(default=20, ge=1, le=10000)
    environment: str = "prod"
    labels: Dict[str, str] = Field(default_factory=dict)


class AgentRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,128}$")
    display_name: str
    owner: str
    service_principal: str
    status: ManagedStatus = ManagedStatus.ACTIVE
    allowed_datasets: Set[str] = Field(default_factory=set)
    allowed_capabilities: Set[Capability] = Field(default_factory=lambda: {Capability.RETRIEVE})
    require_citations: bool = True
    allow_raw_vector_input: bool = False
    max_top_k: int = Field(default=20, ge=1, le=1000)
    max_context_chars: int = Field(default=100000, ge=1000, le=5_000_000)
    mcp_enabled: bool = False


class GuardrailAction(str, Enum):
    DENY = "deny"
    REDACT = "redact"
    LIMIT = "limit"
    AUDIT = "audit"


class GuardrailRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,128}$")
    name: str
    enabled: bool = True
    scope: Literal["global", "dataset", "client", "agent"] = "global"
    target: Optional[str] = None
    kind: Literal[
        "require_citations",
        "deny_sensitive_export",
        "max_scan_budget",
        "max_top_k",
        "prompt_injection_signal",
        "pii_redaction",
        "content_classification",
        "rate_limit",
        "schema_drift",
        "index_freshness",
    ]
    action: GuardrailAction
    config: Dict[str, Any] = Field(default_factory=dict)
    severity: Literal["info", "warning", "high", "critical"] = "high"


class IndexDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,128}$")
    dataset_id: str
    index_type: Literal["keyword", "vector"]
    active_version: str
    candidate_version: Optional[str] = None
    state: Literal["healthy", "building", "validating", "promoting", "degraded", "failed"] = "healthy"
    freshness_lag_seconds: int = Field(default=0, ge=0)
    indexed_records: int = Field(default=0, ge=0)
    shard_count: int = Field(default=1, ge=1)
    replica_count: int = Field(default=2, ge=1)
    traffic_to_candidate_percent: int = Field(default=0, ge=0, le=100)
    last_validation: Optional[str] = None


class ControlOverview(BaseModel):
    datasets: int
    policies: int
    clients: int
    agents: int
    guardrails: int
    indexes: int
    healthy_indexes: int
    degraded_indexes: int
    active_clients: int
    active_agents: int
    control_plane_status: Literal["healthy", "degraded", "down"]
