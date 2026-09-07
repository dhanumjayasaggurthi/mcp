from enterprise_data_platform.backends import DeterministicHashEmbeddingProvider
from enterprise_data_platform.indexing import ChangeEvent, InMemoryCheckpointStore, IndexingPipeline, canonical_chunks
from test_platform_core import product


class Sink:
    def __init__(self):
        self.upserts = []
        self.deletes = []
    def upsert(self, **kwargs): self.upserts.append(kwargs)
    def delete_record(self, **kwargs): self.deletes.append(kwargs)


class VectorSink(Sink):
    pass


def test_chunk_ids_are_deterministic_and_pipeline_is_idempotent():
    p = product()
    e = ChangeEvent(
        event_id="evt-1",
        operation="upsert",
        record_id="doc-1",
        source_version="42",
        values={"id": "doc-1", "tenant_id": "acme", "title": "Title", "body": "word " * 500, "secret": "x"},
    )
    a = canonical_chunks(p, e)
    b = canonical_chunks(p, e)
    assert [x.chunk_id for x in a] == [x.chunk_id for x in b]
    assert all(x.text for x in a)
    assert all(x.metadata["tenant_id"] == "acme" for x in a)

    keyword, vector, chunks = Sink(), VectorSink(), Sink()
    pipeline = IndexingPipeline(
        checkpoints=InMemoryCheckpointStore(),
        keyword_sink=keyword,
        vector_sink=vector,
        chunk_sink=chunks,
        embedder=DeterministicHashEmbeddingProvider(),
    )
    first = pipeline.process(product=p, target_index_version="candidate-v2", events=[e])
    second = pipeline.process(product=p, target_index_version="candidate-v2", events=[e])
    assert first["processed"] == 1 and first["chunks"] > 0
    assert second == {"processed": 0, "skipped": 1, "deleted": 0, "chunks": 0}
    assert all(call["index_version"] == "candidate-v2" for call in keyword.upserts + vector.upserts + chunks.upserts)


def test_delete_is_propagated_to_all_indexes_and_chunk_store():
    p = product()
    keyword, vector, chunks = Sink(), VectorSink(), Sink()
    pipeline = IndexingPipeline(
        checkpoints=InMemoryCheckpointStore(),
        keyword_sink=keyword,
        vector_sink=vector,
        chunk_sink=chunks,
        embedder=DeterministicHashEmbeddingProvider(),
    )
    event = ChangeEvent(event_id="delete-1", operation="delete", record_id="doc-9", source_version="99", values={})
    result = pipeline.process(product=p, target_index_version="candidate-v2", events=[event])
    assert result["deleted"] == 1
    assert len(keyword.deletes) == len(vector.deletes) == len(chunks.deletes) == 1
