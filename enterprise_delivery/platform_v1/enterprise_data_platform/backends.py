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

    def embed_batch(self, texts: Sequence[str], *, profile_id: str, dimensions: int) -> List[List[float]]:
        raise NotImplementedError("production embedding providers must implement batching")


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
    return _filter_value(row, expr) is True


def _filter_value(row, expr):
    """SQL three-valued boolean semantics; only TRUE authorizes a row."""
    if not expr:
        return True
    if 'and' in expr or 'or' in expr:
        key = 'and' if 'and' in expr else 'or'
        values = [_filter_value(row, child) for child in expr[key]]
        if key == 'and':
            return False if False in values else (None if None in values else True)
        return True if True in values else (None if None in values else False)
    if 'not' in expr:
        value = _filter_value(row, expr['not'])
        return None if value is None else not value
    actual, op, value = row.get(expr['field']), expr['op'], expr.get('value')
    for part in expr.get('path', []):
        try:
            actual = actual[part] if isinstance(actual, dict) and isinstance(part, str) or isinstance(actual, list) and type(part) is int else None
        except (KeyError, IndexError, TypeError): actual = None
    if op == 'exists':
        return (actual is not None) == (True if value is None else value)
    if op in {'eq','neq'} and value is None:
        return (actual is None) if op == 'eq' else (actual is not None)
    if actual is None:
        return None
    if 'path' in expr:
        sample = next((v for v in value if v is not None), None) if isinstance(value, list) else value
        compatible = type(actual) is type(sample) or type(actual) in {int, float} and type(sample) in {int, float}
        if sample is not None and not compatible: return None
    from datetime import date, datetime
    if isinstance(actual, (date, datetime)):
        convert = datetime.fromisoformat if isinstance(actual, datetime) else date.fromisoformat
        value = [convert(v) if isinstance(v, str) else v for v in value] if isinstance(value, list) else convert(value) if isinstance(value, str) else value
    if op == 'eq': return actual == value
    if op == 'neq': return actual != value
    if op == 'gt': return actual > value
    if op == 'gte': return actual >= value
    if op == 'lt': return actual < value
    if op == 'lte': return actual <= value
    if op in {'in', 'not_in'}:
        found = True if actual in value else (None if None in value else False)
        return found if op == 'in' or found is None else not found
    if op == 'between': return value[0] <= actual <= value[1]
    if op == 'contains': return str(value).lower() in str(actual).lower()
    if op == 'starts_with': return str(actual).lower().startswith(str(value).lower())
    if op == 'ends_with': return str(actual).lower().endswith(str(value).lower())
    if op == 'array_is_empty': return (len(actual) == 0) == (value is not False)
    if op == 'array_contains_all': return all(v in actual for v in value)
    if op == 'array_overlaps': return any(v in actual for v in value)
    if op == 'json_contains':
        def contains(a, b):
            if isinstance(b, dict): return isinstance(a, dict) and all(k in a and contains(a[k], v) for k, v in b.items())
            if isinstance(b, list): return isinstance(a, list) and all(any(contains(x, v) for x in a) for v in b)
            return a == b and (type(a) is type(b) or type(a) in {int, float} and type(b) in {int, float})
        return contains(actual, value)
    raise ValueError('unsupported filter operation')


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
            if a.get(item.field) is None or b.get(item.field) is None:
                return cmp  # NULLS LAST in both directions
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

    def embed_batch(self, texts, *, profile_id, dimensions):
        return [self.embed(t, profile_id=profile_id, dimensions=dimensions) for t in texts]

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

