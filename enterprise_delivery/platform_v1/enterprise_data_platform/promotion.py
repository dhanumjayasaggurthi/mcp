from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .control_models import IndexDeployment


class PromotionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalQualityEvidence:
    evaluated_queries: int
    recall_at_k: float
    precision_at_k: float
    citation_coverage: float
    p95_latency_ms: float
    error_rate: float

    def __post_init__(self):
        import math
        if self.evaluated_queries < 0 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in
            [self.recall_at_k, self.precision_at_k, self.citation_coverage, self.error_rate]):
            raise ValueError('invalid retrieval quality evidence')
        if not math.isfinite(self.p95_latency_ms) or self.p95_latency_ms < 0:
            raise ValueError('invalid latency evidence')


@dataclass(frozen=True)
class PromotionThresholds:
    min_queries: int = 100
    min_recall_at_k: float = 0.90
    min_precision_at_k: float = 0.80
    min_citation_coverage: float = 0.99
    max_p95_latency_ms: float = 1500.0
    max_error_rate: float = 0.001
    max_freshness_lag_seconds: int = 300
    min_replicas: int = 2


class IndexPromotionController:
    """Zero-downtime index promotion guard.

    Build and validate a candidate beside the active index. Only after quality,
    freshness and availability thresholds pass may traffic move to the
    candidate. Final promotion is an alias/version swap; the active version is
    never mutated in place.
    """

    def __init__(self, thresholds: Optional[PromotionThresholds] = None) -> None:
        self.thresholds = thresholds or PromotionThresholds()

    def validate_candidate(self, deployment: IndexDeployment, evidence: RetrievalQualityEvidence) -> IndexDeployment:
        if not deployment.candidate_version:
            raise PromotionBlocked("candidate_version is required")
        t = self.thresholds
        failures = []
        if evidence.evaluated_queries < t.min_queries:
            failures.append(f"evaluated_queries {evidence.evaluated_queries} < {t.min_queries}")
        if evidence.recall_at_k < t.min_recall_at_k:
            failures.append(f"recall_at_k {evidence.recall_at_k:.4f} < {t.min_recall_at_k:.4f}")
        if evidence.precision_at_k < t.min_precision_at_k:
            failures.append(f"precision_at_k {evidence.precision_at_k:.4f} < {t.min_precision_at_k:.4f}")
        if evidence.citation_coverage < t.min_citation_coverage:
            failures.append(f"citation_coverage {evidence.citation_coverage:.4f} < {t.min_citation_coverage:.4f}")
        if evidence.p95_latency_ms > t.max_p95_latency_ms:
            failures.append(f"p95_latency_ms {evidence.p95_latency_ms:.1f} > {t.max_p95_latency_ms:.1f}")
        if evidence.error_rate > t.max_error_rate:
            failures.append(f"error_rate {evidence.error_rate:.6f} > {t.max_error_rate:.6f}")
        if deployment.freshness_lag_seconds > t.max_freshness_lag_seconds:
            failures.append(f"freshness_lag_seconds {deployment.freshness_lag_seconds} > {t.max_freshness_lag_seconds}")
        if deployment.replica_count < t.min_replicas:
            failures.append(f"replica_count {deployment.replica_count} < {t.min_replicas}")
        if failures:
            raise PromotionBlocked("; ".join(failures))
        return deployment.model_copy(update={"state": "validating", "last_validation": datetime.now(timezone.utc).isoformat()})

    def set_canary(self, deployment: IndexDeployment, percent: int) -> IndexDeployment:
        if not deployment.candidate_version:
            raise PromotionBlocked("candidate_version is required")
        if deployment.state not in {"validating", "promoting"}:
            raise PromotionBlocked(f"candidate must be validated before canary; current state={deployment.state}")
        if percent < 0 or percent > 100:
            raise ValueError("canary percent must be 0..100")
        return deployment.model_copy(update={"state": "promoting", "traffic_to_candidate_percent": percent})

    def promote(self, deployment: IndexDeployment) -> IndexDeployment:
        if not deployment.candidate_version:
            raise PromotionBlocked("candidate_version is required")
        if deployment.state != "promoting" or deployment.traffic_to_candidate_percent != 100:
            raise PromotionBlocked("candidate must successfully serve 100% canary traffic before final promotion")
        return deployment.model_copy(
            update={
                "active_version": deployment.candidate_version,
                "candidate_version": None,
                "state": "healthy",
                "traffic_to_candidate_percent": 0,
            }
        )

    def rollback_canary(self, deployment: IndexDeployment) -> IndexDeployment:
        # Alias routing goes back to the unchanged active version immediately.
        return deployment.model_copy(update={"state": "validating" if deployment.candidate_version else "healthy", "traffic_to_candidate_percent": 0})

