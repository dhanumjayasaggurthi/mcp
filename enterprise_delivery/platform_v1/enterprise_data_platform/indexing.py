from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence

from .backends import EmbeddingProvider
from .models import DataProduct


@dataclass(frozen=True)
class ChangeEvent:
    event_id: str
    operation: str  # upsert | delete
    record_id: str
    source_version: str
    values: Dict[str, Any]
    acl: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalChunk:
    record_id: str
    chunk_id: str
    ordinal: int
    text: str
    metadata: Dict[str, Any]
    content_hash: str
    source_version: str
    dataset_id: str = ''
    dataset_version: str = ''
    ingestion_timestamp: str = ''
    acl: Dict[str, Any] = field(default_factory=dict)


class KeywordIndexSink(Protocol):
    def upsert(self, *, dataset_id: str, index_version: str, chunks: Sequence[CanonicalChunk]) -> None: ...
    def delete_record(self, *, dataset_id: str, index_version: str, record_id: str) -> None: ...


class VectorIndexSink(Protocol):
    def upsert(self, *, dataset_id: str, index_version: str, chunks: Sequence[CanonicalChunk], vectors: Sequence[Sequence[float]]) -> None: ...
    def delete_record(self, *, dataset_id: str, index_version: str, record_id: str) -> None: ...


class ChunkStoreSink(Protocol):
    def upsert(self, *, dataset_id: str, index_version: str, chunks: Sequence[CanonicalChunk]) -> None: ...
    def delete_record(self, *, dataset_id: str, index_version: str, record_id: str) -> None: ...


class CheckpointStore(Protocol):
    def seen(self, dataset_id: str, event_id: str) -> bool: ...
    def mark(self, dataset_id: str, event_id: str) -> None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()
        self._lock = threading.RLock()

    def seen(self, dataset_id: str, event_id: str) -> bool:
        with self._lock:
            return (dataset_id, event_id) in self._seen

    def mark(self, dataset_id: str, event_id: str) -> None:
        with self._lock:
            self._seen.add((dataset_id, event_id))


def materialize_text(product: DataProduct, values: Dict[str, Any]) -> str:
    if not product.retrieval or not product.retrieval.text:
        raise ValueError("dataset has no text profile")
    profile = product.retrieval.text
    parts: List[str] = []
    for field in profile.source_fields:
        value = values.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def chunk_text(text: str, *, size: int, overlap: int) -> List[str]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("invalid chunk size/overlap")
    text = text.strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            # Prefer a nearby whitespace boundary without shrinking the chunk too
            # aggressively; deterministic boundaries are essential for stable IDs.
            boundary = text.rfind(" ", start + max(1, size // 2), end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        next_start = max(start + 1, end - overlap)
        # Avoid beginning in the middle of a word.
        while next_start < n and next_start > 0 and not text[next_start - 1].isspace() and not text[next_start].isspace():
            next_start += 1
        start = min(next_start, n)
    return chunks


def canonical_chunks(product: DataProduct, event: ChangeEvent) -> List[CanonicalChunk]:
    if event.operation != "upsert":
        return []
    if not product.retrieval or not product.retrieval.text:
        raise ValueError("dataset has no text profile")
    text = materialize_text(product, event.values)
    profile = product.retrieval.text
    pieces = chunk_text(text, size=profile.chunk_size_chars, overlap=profile.chunk_overlap_chars)
    metadata_fields = {
        f.name for f in product.fields if f.vector_metadata or f.filterable
    }
    metadata = {k: event.values.get(k) for k in metadata_fields if k in event.values}
    chunks: List[CanonicalChunk] = []
    for ordinal, piece in enumerate(pieces):
        digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()
        # Namespace identities by tenant, product and schema version. Identical
        # content within a record keeps its ID when only source time changes.
        identity = [product.id, product.version, event.values.get(product.tenant_field) if product.tenant_field else None,
            event.record_id, ordinal, digest]
        chunk_id = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
        chunks.append(
            CanonicalChunk(
                record_id=event.record_id,
                chunk_id=chunk_id,
                ordinal=ordinal,
                text=piece,
                metadata=dict(metadata),
                content_hash=digest,
                source_version=event.source_version,
                dataset_id=product.id,
                dataset_version=product.version,
                ingestion_timestamp=datetime.now(timezone.utc).isoformat(),
                acl=event.acl,
            )
        )
    return chunks


class IndexingPipeline:
    """Idempotent candidate-index writer for snapshot/CDC events.

    The target index version is explicit. This prevents background reindexing
    from mutating the currently active index in place and enables canary/promotion
    through IndexPromotionController.
    """

    def __init__(
        self,
        *,
        checkpoints: CheckpointStore,
        keyword_sink: KeywordIndexSink,
        vector_sink: VectorIndexSink,
        chunk_sink: ChunkStoreSink,
        embedder: EmbeddingProvider,
    ) -> None:
        self.checkpoints = checkpoints
        self.keyword_sink = keyword_sink
        self.vector_sink = vector_sink
        self.chunk_sink = chunk_sink
        self.embedder = embedder

    def process(self, *, product: DataProduct, target_index_version: str, events: Iterable[ChangeEvent]) -> Dict[str, int]:
        if not product.retrieval or not product.retrieval.vector:
            raise ValueError("indexing pipeline requires vector profile")
        counts = {"processed": 0, "skipped": 0, "deleted": 0, "chunks": 0}
        profile = product.retrieval.vector
        for event in events:
            checkpoint_scope = product.id + ':' + target_index_version
            if self.checkpoints.seen(checkpoint_scope, event.event_id):
                counts["skipped"] += 1
                continue
            if event.operation == "delete":
                self.keyword_sink.delete_record(dataset_id=product.id, index_version=target_index_version, record_id=event.record_id)
                self.vector_sink.delete_record(dataset_id=product.id, index_version=target_index_version, record_id=event.record_id)
                self.chunk_sink.delete_record(dataset_id=product.id, index_version=target_index_version, record_id=event.record_id)
                counts["deleted"] += 1
            elif event.operation == "upsert":
                chunks = canonical_chunks(product, event)
                vectors = self.embedder.embed_batch([c.text for c in chunks], profile_id=profile.profile_id, dimensions=profile.dimensions)
                # Write the canonical clear text before indexes. If an index write
                # later fails, the event is not checkpointed and is safely retried.
                self.chunk_sink.upsert(dataset_id=product.id, index_version=target_index_version, chunks=chunks)
                self.keyword_sink.upsert(dataset_id=product.id, index_version=target_index_version, chunks=chunks)
                self.vector_sink.upsert(dataset_id=product.id, index_version=target_index_version, chunks=chunks, vectors=vectors)
                counts["chunks"] += len(chunks)
            else:
                raise ValueError(f"unsupported change operation '{event.operation}'")
            self.checkpoints.mark(checkpoint_scope, event.event_id)
            counts["processed"] += 1
        return counts
