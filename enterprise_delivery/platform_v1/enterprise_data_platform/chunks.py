"""Canonical clear text with batched hydration and committed-record verification."""
from dataclasses import asdict
from sqlalchemy import delete, insert, select, update
from .backends import CanonicalChunkStore
from .durable import chunks, fingerprint, records
from .governor import ensure_row


def record_key(product, record_id, metadata):
    tenant = metadata.get(product.tenant_field) if product.tenant_field else None
    return fingerprint([tenant, record_id])


def retrieval_version(product):
    if product.retrieval and product.retrieval.vector:
        return product.retrieval.vector.index_version
    if product.retrieval and product.retrieval.keyword_index:
        return product.retrieval.keyword_index
    raise ValueError('retrieval index version is required')


class SQLChunkStore(CanonicalChunkStore):
    """Concrete PostgreSQL path; shard the engine provider by dataset at scale.

    Engine provider must be deterministic and shared by all API/worker replicas.
    No client controls placement. Plain text is absent from vector documents.
    """
    def __init__(self, store):
        self.store = store

    def get_chunks(self, *, product, chunk_ids):
        ids = list(dict.fromkeys(chunk_ids))
        if len(ids) > 1000:
            raise ValueError('hydration batch exceeds 1000 chunks')
        result = {}
        version = retrieval_version(product)
        with self.store.engine.connect() as conn:
            for start in range(0, len(ids), 200):
                stmt = select(chunks.c.id, chunks.c.payload, records.c.sequence, records.c.deleted).join(records,
                    (chunks.c.dataset == records.c.dataset) & (chunks.c.version == records.c.version) & (chunks.c.record == records.c.record))
                rows = conn.execute(stmt.where(chunks.c.dataset == product.id, chunks.c.version == version,
                    chunks.c.id.in_(ids[start:start+200]))).mappings()
                for row in rows:
                    value = row['payload']
                    if row['deleted'] or str(value['sequence']) != str(row['sequence']):
                        continue
                    if value['dataset_version'] != product.version:
                        continue
                    result[row['id']] = value
        return result

    def replace_record(self, conn, *, product, version, key, canonical, sequence, acl=None):
        conn.execute(delete(chunks).where(chunks.c.dataset == product.id, chunks.c.version == version, chunks.c.record == key))
        values = []
        for chunk in canonical:
            payload = asdict(chunk)
            payload.update(dataset_id=product.id, dataset_version=product.version, sequence=str(sequence), acl=acl or {},
                embedding_profile=product.retrieval.vector.profile_id if product.retrieval.vector else None,
                provenance={'dataset': product.id, 'record_id': chunk.record_id, 'source_version': chunk.source_version})
            values.append(dict(dataset=product.id, version=version, id=chunk.chunk_id, record=key, payload=payload))
        if values:
            conn.execute(insert(chunks), values)
