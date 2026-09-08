"""Partitionable snapshot/CDC bridge and transactionally fenced ingestion worker."""
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from .chunks import record_key
from .context import ExecutionContext, current_actor, execution_context
from .durable import checkpoints, fingerprint, records
from .governor import ensure_row
from .indexing import ChangeEvent, canonical_chunks
from .models import Principal


class IngestionEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: str
    dataset_version: str
    index_version: str
    partition: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0, le=9_000_000_000_000_000_000)
    event_id: str = Field(min_length=1, max_length=256)
    operation: str = Field(pattern='^(upsert|delete)$')
    record_id: str = Field(min_length=1, max_length=512)
    source_version: str
    values: dict = Field(default_factory=dict)
    acl: dict[str, list[str]] = Field(default_factory=dict)


class IngestionBridge:
    def __init__(self, queue):
        self.queue = queue

    def publish(self, event: IngestionEvent):
        # A source offset may advance only after this transaction succeeds.
        # Queue payload contains bounded data; bulk snapshots publish in batches.
        return self.queue.enqueue('ingestion', fingerprint([event.dataset_id, event.index_version]),
            event.model_dump(mode='json'), idempotency_key=fingerprint([event.partition, event.event_id]))

    def publish_page(self, events: list[IngestionEvent], *, stream: str, resume_token: dict, expected_revision: int):
        """Commit a bounded snapshot/CDC page and its source offset together.

        The connector owns source offset semantics. This is a publication
        checkpoint, separate from the workers' possibly out-of-order progress.
        A crashed publisher resumes from this durable token and may replay IDs.
        """
        if not events or len(events) > 100:
            raise ValueError('publication page must contain 1..100 events')
        first = events[0]
        if any((e.dataset_id, e.index_version, e.partition) !=
               (first.dataset_id, first.index_version, first.partition) for e in events):
            raise ValueError('publication page must belong to one index and source partition')
        scope = fingerprint([first.dataset_id, first.index_version])
        checkpoint_id = fingerprint([scope, first.partition, stream])
        with self.queue.store.engine.begin() as conn:
            for event in events:
                self.queue.enqueue('ingestion', scope, event.model_dump(mode='json'),
                    idempotency_key=fingerprint([event.partition, event.event_id]), conn=conn)
            revision = self.queue.store.put('source_offsets', checkpoint_id,
                {'dataset_id': first.dataset_id, 'index_version': first.index_version,
                 'partition': first.partition, 'stream': stream, 'resume_token': resume_token},
                expected_revision=expected_revision, conn=conn)
            return {'checkpoint_id': checkpoint_id, 'revision': revision, 'resume_token': resume_token}


class IngestionWorker:
    def __init__(self, *, queue, catalog, chunk_store, keyword_sink, vector_sink, embedder, governor=None):
        self.queue, self.catalog, self.chunks = queue, catalog, chunk_store
        self.keyword, self.vector, self.embedder = keyword_sink, vector_sink, embedder
        self.governor = governor

    def process(self, job):
        from contextlib import nullcontext
        event = IngestionEvent.model_validate(job['payload'])
        product = self.catalog.get(event.dataset_id)
        tenant = event.values.get(product.tenant_field) if product.tenant_field else None
        actor = current_actor.set({'subject': 'indexing-worker', 'client_id': 'indexing-worker',
            'tenant': tenant, 'dataset_id': product.id, 'index_version': event.index_version})
        try:
            with execution_context(ExecutionContext(workload='indexing')) as context:
                principal = Principal(subject='indexing-worker', client_id='indexing-worker', tenant=tenant)
                admission = self.governor.admit(principal, product.source.source_id or product.source.connector,
                    'indexing', context) if self.governor else nullcontext()
                with admission:
                    return self._process(job, event, product, context)
        finally:
            current_actor.reset(actor)

    def _process(self, job, event, product, context):
        if product.version != event.dataset_version:
            raise ValueError('dataset schema version changed; replan ingestion')
        if set(event.acl) - {'subjects', 'groups'}:
            raise ValueError('unsupported chunk ACL')
        if product.tenant_field and not event.values.get(product.tenant_field):
            raise ValueError('ingestion requires tenant identity, including tombstones')
        if not product.retrieval or not product.retrieval.vector:
            raise ValueError('distributed pipeline requires a retrieval profile')
        text_size = sum(len(str(v)) for v in event.values.values())
        if text_size > 200_000:
            raise ValueError('record exceeds ingestion budget; split upstream')
        key = record_key(product, event.record_id, event.values)
        with self.queue.store.engine.begin() as conn:
            # Bounded one-record transaction: job + record lock across all sinks.
            # A crashed worker rolls canonical writes/checkpoint back. External
            # sinks use source versions and are replayed; readers trust only the
            # committed canonical record, so partial writes cannot expose data.
            self.queue._owned(conn, job, lock=True)
            ensure_row(conn, records, dict(dataset=product.id, version=event.index_version, record=key,
                sequence='-1', event_id='', deleted=False, chunk_ids=[]))
            condition = [records.c.dataset == product.id, records.c.version == event.index_version, records.c.record == key]
            previous = conn.execute(select(records).where(*condition).with_for_update()).mappings().one()
            if event.sequence <= int(previous['sequence']):
                if event.sequence == int(previous['sequence']) and previous['event_id'] != event.event_id:
                    raise ValueError('conflicting events have the same source sequence')
                self.queue.complete(job, {'skipped': True, 'sequence': event.sequence}, conn=conn)
                return
            change = ChangeEvent(event.event_id, event.operation, event.record_id, event.source_version, event.values, event.acl)
            canonical = canonical_chunks(product, change)
            if len(canonical) > 1000:
                raise ValueError('record exceeds chunk budget')
            profile = product.retrieval.vector
            vectors = self.embedder.embed_batch([c.text for c in canonical], profile_id=profile.profile_id, dimensions=profile.dimensions)
            if len(vectors) != len(canonical):
                raise ValueError('embedding count does not match chunks')
            for sink in [self.keyword, self.vector]:
                context.remaining()
                sink.write_record(product=product, version=event.index_version, canonical=canonical, vectors=vectors,
                    sequence=event.sequence, old_chunk_ids=previous['chunk_ids'])
            context.remaining()
            self.chunks.replace_record(conn, product=product, version=event.index_version, key=key,
                canonical=canonical, sequence=event.sequence, acl=event.acl)
            conn.execute(update(records).where(*condition).values(sequence=str(event.sequence), event_id=event.event_id,
                deleted=event.operation == 'delete', chunk_ids=[c.chunk_id for c in canonical]))
            ensure_row(conn, checkpoints, dict(dataset=product.id, version=event.index_version, partition=event.partition, sequence='-1'))
            checkpoint_condition = [checkpoints.c.dataset == product.id, checkpoints.c.version == event.index_version,
                checkpoints.c.partition == event.partition]
            previous_checkpoint = conn.execute(select(checkpoints).where(*checkpoint_condition).with_for_update()).mappings().one()
            conn.execute(update(checkpoints).where(*checkpoint_condition).values(sequence=str(max(event.sequence, int(previous_checkpoint['sequence'])))))
            self.queue.complete(job, {'chunks': len(canonical), 'sequence': event.sequence, 'partition': event.partition}, conn=conn)
