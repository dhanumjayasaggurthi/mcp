from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from .models import RetrievalHit


def rrf_fuse(
    keyword_hits: Iterable[RetrievalHit],
    vector_hits: Iterable[RetrievalHit],
    *,
    k: int = 60,
    keyword_weight: float = 1.0,
    vector_weight: float = 1.0,
    top_k: int = 20,
) -> List[RetrievalHit]:
    """Weighted reciprocal-rank fusion with deterministic de-duplication."""

    if k <= 0 or top_k <= 0:
        raise ValueError("k and top_k must be positive")

    merged: Dict[Tuple[str, str], RetrievalHit] = {}
    fusion: Dict[Tuple[str, str], float] = {}

    def add(hits: Iterable[RetrievalHit], label: str, weight: float) -> None:
        for rank, source_hit in enumerate(hits, start=1):
            key = (source_hit.record_id, source_hit.chunk_id or "")
            if key not in merged:
                merged[key] = source_hit.model_copy(deep=True)
                merged[key].scores = dict(source_hit.scores)
            else:
                existing = merged[key]
                # Preserve richer payload from either route without clobbering.
                if existing.text is None and source_hit.text is not None:
                    existing.text = source_hit.text
                existing.metadata = {**source_hit.metadata, **existing.metadata}
                existing.source = {**source_hit.source, **existing.source}
                existing.scores.update(source_hit.scores)
                existing.ranks.update(source_hit.ranks)
            merged[key].ranks.setdefault(label, rank)
            contribution = weight / (k + rank)
            fusion[key] = fusion.get(key, 0.0) + contribution
            merged[key].scores[f"rrf_{label}"] = contribution

    add(keyword_hits, "keyword", keyword_weight)
    add(vector_hits, "vector", vector_weight)

    output: List[RetrievalHit] = []
    for key, hit in merged.items():
        hit.score = fusion[key]
        hit.scores["hybrid"] = fusion[key]
        output.append(hit)
    output.sort(key=lambda h: (-h.score, h.record_id, h.chunk_id or ""))
    return output[:top_k]

