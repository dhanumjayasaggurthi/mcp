from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .models import CountMode, DataProduct, RetrievalHit, SortField


@dataclass
class StructuredPage:
    rows: List[Dict[str, Any]]
    next_position: Optional[Dict[str, Any]] = None
    count: Optional[int] = None
    count_is_estimate: bool = False


class StructuredBackend(ABC):
    @abstractmethod
    def query(
        self,
        *,
        product: DataProduct,
        fields: Sequence[str],
        filter_expr: Optional[Dict[str, Any]],
        order_by: Sequence[SortField],
        limit: int,
        position: Optional[Mapping[str, Any]],
        count_mode: CountMode,
    ) -> StructuredPage:
        raise NotImplementedError


class KeywordBackend(ABC):
    @abstractmethod
    def search(
        self,
        *,
        product: DataProduct,
        query: str,
        filter_expr: Optional[Dict[str, Any]],
        top_k: int,
    ) -> List[RetrievalHit]:
        raise NotImplementedError


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str, *, profile_id: str, dimensions: int) -> List[float]:
        raise NotImplementedError


class VectorBackend(ABC):
    @abstractmethod
    def search(
        self,
        *,
        product: DataProduct,
        vector: Sequence[float],
        filter_expr: Optional[Dict[str, Any]],
        top_k: int,
    ) -> List[RetrievalHit]:
        raise NotImplementedError


class CanonicalChunkStore(ABC):
    @abstractmethod
    def get_chunks(self, *, product: DataProduct, chunk_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError


class ExportBackend(ABC):
    @abstractmethod
    def submit(self, *, product: DataProduct, principal_subject: str, fields: Sequence[str], filter_expr: Optional[Dict[str, Any]], format: str, compression: Optional[str]):
        raise NotImplementedError


def eval_filter(row: Mapping[str, Any], expr: Optional[Dict[str, Any]]) -> bool:
    if not expr:
        return True
    if "and" in expr:
        return all(eval_filter(row, child) for child in expr["and"])
    if "or" in expr:
        return any(eval_filter(row, child) for child in expr["or"])
    if "not" in expr:
        return not eval_filter(row, expr["not"])
    field = expr["field"]
    op = expr["op"]
    value = expr.get("value")
    actual = row.get(field)
    if op == "eq":
        return actual == value
    if op == "neq":
        return actual != value
    if op == "gt":
        return actual is not None and actual > value
    if op == "gte":
        return actual is not None and actual >= value
    if op == "lt":
        return actual is not None and actual < value
    if op == "lte":
        return actual is not None and actual <= value
    if op == "in":
        return actual in value
    if op == "between":
        return actual is not None and value[0] <= actual <= value[1]
    if op == "contains":
        return actual is not None and str(value).lower() in str(actual).lower()
    if op == "starts_with":
        return actual is not None and str(actual).lower().startswith(str(value).lower())
    if op == "exists":
        exists = actual is not None
        return exists if value is None else exists == bool(value)
    raise ValueError(f"unsupported filter op: {op}")


def _compare_scalar(a: Any, b: Any) -> int:
    if a is None and b is None:
        return 0
    if a is None:
        return 1
    if b is None:
        return -1
    try:
        return (a > b) - (a < b)
    except TypeError:
        sa, sb = str(a), str(b)
        return (sa > sb) - (sa < sb)


def compare_rows(a: Mapping[str, Any], b: Mapping[str, Any], order_by: Sequence[SortField]) -> int:
    for item in order_by:
        cmp = _compare_scalar(a.get(item.field), b.get(item.field))
        if cmp:
            return cmp if item.direction == "asc" else -cmp
    return 0


class InMemoryStructuredBackend(StructuredBackend):
    def __init__(self, rows_by_dataset: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self.rows_by_dataset = rows_by_dataset or {}

    def query(
        self,
        *,
        product: DataProduct,
        fields: Sequence[str],
        filter_expr: Optional[Dict[str, Any]],
        order_by: Sequence[SortField],
        limit: int,
        position: Optional[Mapping[str, Any]],
        count_mode: CountMode,
    ) -> StructuredPage:
        source = self.rows_by_dataset.get(product.id, [])
        filtered = [row for row in source if eval_filter(row, filter_expr)]
        filtered.sort(key=cmp_to_key(lambda a, b: compare_rows(a, b, order_by)))
        if position:
            filtered = [row for row in filtered if compare_rows(row, position, order_by) > 0]
        selected = filtered[: limit + 1]
        has_more = len(selected) > limit
        page_rows = selected[:limit]
        projected = [{field: row.get(field) for field in fields} for row in page_rows]
        next_position = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_position = {item.field: last.get(item.field) for item in order_by}
        count = None
        estimate = False
        if count_mode == CountMode.EXACT:
            count = sum(1 for row in source if eval_filter(row, filter_expr))
        elif count_mode == CountMode.ESTIMATE:
            # Reference adapter has exact knowledge; mark estimate to preserve the
            # requested contract and mimic warehouse statistics behavior.
            count = sum(1 for row in source if eval_filter(row, filter_expr))
            estimate = True
        return StructuredPage(
            rows=projected,
            next_position=next_position,
            count=count,
            count_is_estimate=estimate,
        )


class InMemoryCanonicalChunkStore(CanonicalChunkStore):
    def __init__(self, chunks_by_dataset: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None) -> None:
        self.chunks_by_dataset = chunks_by_dataset or {}

    def get_chunks(self, *, product: DataProduct, chunk_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        source = self.chunks_by_dataset.get(product.id, {})
        return {chunk_id: dict(source[chunk_id]) for chunk_id in chunk_ids if chunk_id in source}


class InMemoryKeywordBackend(KeywordBackend):
    def __init__(self, hits_by_dataset: Optional[Dict[str, List[RetrievalHit]]] = None) -> None:
        self.hits_by_dataset = hits_by_dataset or {}

    def search(self, *, product: DataProduct, query: str, filter_expr: Optional[Dict[str, Any]], top_k: int) -> List[RetrievalHit]:
        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        ranked: List[RetrievalHit] = []
        for hit in self.hits_by_dataset.get(product.id, []):
            candidate = hit.model_copy(deep=True)
            row = candidate.metadata
            if not eval_filter(row, filter_expr):
                continue
            haystack = " ".join([candidate.text or "", *[str(v) for v in candidate.metadata.values()]]).lower()
            score = float(sum(haystack.count(term) for term in terms))
            if score <= 0:
                continue
            candidate.score = score
            candidate.scores["keyword"] = score
            ranked.append(candidate)
        ranked.sort(key=lambda h: (-h.score, h.record_id, h.chunk_id or ""))
        return ranked[:top_k]


class DeterministicHashEmbeddingProvider(EmbeddingProvider):
    """Test/reference embedder only; never use this as a semantic production model."""

    def embed(self, text: str, *, profile_id: str, dimensions: int) -> List[float]:
        import hashlib

        seed = (profile_id + "\0" + text).encode("utf-8")
        out: List[float] = []
        counter = 0
        while len(out) < dimensions:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            for byte in digest:
                out.append((byte / 127.5) - 1.0)
                if len(out) == dimensions:
                    break
            counter += 1
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]


class InMemoryVectorBackend(VectorBackend):
    def __init__(self, entries_by_dataset: Optional[Dict[str, List[tuple[RetrievalHit, List[float]]]]] = None) -> None:
        self.entries_by_dataset = entries_by_dataset or {}

    @staticmethod
    def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b):
            raise ValueError("vector dimension mismatch")
        dot = sum(float(x) * float(y) for x, y in zip(a, b))
        na = math.sqrt(sum(float(x) * float(x) for x in a))
        nb = math.sqrt(sum(float(y) * float(y) for y in b))
        return dot / ((na * nb) or 1.0)

    def search(self, *, product: DataProduct, vector: Sequence[float], filter_expr: Optional[Dict[str, Any]], top_k: int) -> List[RetrievalHit]:
        ranked: List[RetrievalHit] = []
        for base_hit, candidate_vector in self.entries_by_dataset.get(product.id, []):
            hit = base_hit.model_copy(deep=True)
            if not eval_filter(hit.metadata, filter_expr):
                continue
            score = self._cosine(vector, candidate_vector)
            hit.score = score
            hit.scores["vector"] = score
            ranked.append(hit)
        ranked.sort(key=lambda h: (-h.score, h.record_id, h.chunk_id or ""))
        return ranked[:top_k]
