from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Set

from .models import RetrievalHit
from .promotion import RetrievalQualityEvidence


@dataclass(frozen=True)
class GoldenQueryResult:
    query_id: str
    relevant_record_ids: Set[str]
    retrieved: Sequence[RetrievalHit]
    latency_ms: float
    failed: bool = False


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return ordered[rank]


def evaluate_retrieval(results: Iterable[GoldenQueryResult], *, k: int) -> RetrievalQualityEvidence:
    if k <= 0:
        raise ValueError("k must be positive")
    items = list(results)
    if not items:
        raise ValueError("at least one golden query result is required")

    recalls: List[float] = []
    precisions: List[float] = []
    cited = 0
    returned = 0
    latencies: List[float] = []
    failures = 0

    for item in items:
        latencies.append(float(item.latency_ms))
        if item.failed:
            failures += 1
            recalls.append(0.0)
            precisions.append(0.0)
            continue
        hits = list(item.retrieved[:k])
        retrieved_ids = {h.record_id for h in hits}
        relevant = item.relevant_record_ids
        true_positive = len(retrieved_ids & relevant)
        recalls.append(true_positive / len(relevant) if relevant else 1.0)
        precisions.append(true_positive / len(hits) if hits else (1.0 if not relevant else 0.0))
        returned += len(hits)
        cited += sum(1 for h in hits if h.source.get("dataset") and h.source.get("record_id"))

    return RetrievalQualityEvidence(
        evaluated_queries=len(items),
        recall_at_k=sum(recalls) / len(recalls),
        precision_at_k=sum(precisions) / len(precisions),
        citation_coverage=(cited / returned) if returned else 1.0,
        p95_latency_ms=_percentile(latencies, 0.95),
        error_rate=failures / len(items),
    )
