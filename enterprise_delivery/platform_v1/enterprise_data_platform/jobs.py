"""Durable bounded queue with retry leases, fencing, idempotency and DLQ states."""
from __future__ import annotations
from contextlib import nullcontext
import random
import uuid
from sqlalchemy import and_, insert, or_, select, update
from .catalog import CatalogConflict
from .control_state import ResourceNotFound
from .durable import db_now, fingerprint, jobs, now_iso, queue_state
from .governor import Overloaded


class LeaseLost(RuntimeError):
    pass


class DurableQueue:
    def __init__(self, store, *, capacity=10000, lease_seconds=60, max_attempts=5):
        if min(capacity, lease_seconds, max_attempts) <= 0:
            raise ValueError('queue bounds must be positive')
        self.store, self.capacity = store, capacity
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts

    def enqueue(self, kind, scope, payload, *, idempotency_key=None, conn=None):
        from .durable import canonical_json
        if len(canonical_json(payload).encode()) > 1_000_000:
            raise ValueError('job descriptor exceeds byte budget')
        key = idempotency_key or str(uuid.uuid4())
        if len(key) > 128:
            raise ValueError('idempotency key exceeds 128 characters')
        digest = fingerprint({k:v for k,v in payload.items() if k != 'trace_id'})
        with (nullcontext(conn) if conn is not None else self.store.engine.begin()) as conn:
            state = conn.execute(select(queue_state).where(queue_state.c.kind == kind).with_for_update()).mappings().first()
            if not state:
                raise ValueError('queue kind is not provisioned')
            existing = conn.execute(select(jobs).where(jobs.c.kind == kind, jobs.c.scope == scope, jobs.c.idempotency == key)).mappings().first()
            if existing:
                if existing['fingerprint'] != digest:
                    raise CatalogConflict('idempotency key was used with a different request')
                return dict(existing)
            if state['outstanding'] >= self.capacity:
                raise Overloaded('durable queue capacity exceeded')
            now = now_iso()
            value = dict(id=str(uuid.uuid4()), kind=kind, scope=scope, idempotency=key, fingerprint=digest,
                payload=payload, status='queued', result=None, attempts=0, available_at=db_now(conn), lease_until=0,
                token=None, created_at=now, updated_at=now, error=None)
            conn.execute(insert(jobs).values(**value))
            conn.execute(update(queue_state).where(queue_state.c.kind == kind).values(outstanding=queue_state.c.outstanding + 1))
            self.store.audit('job.submit', value['id'], {'kind': kind}, conn=conn)
            return value

    def get(self, job_id, *, scope=None):
        with self.store.engine.connect() as conn:
            stmt = select(jobs).where(jobs.c.id == job_id)
            if scope is not None:
                stmt = stmt.where(jobs.c.scope == scope)
            row = conn.execute(stmt).mappings().first()
        if not row:
            raise ResourceNotFound(job_id)
        return dict(row)

    def claim(self, kind):
        with self.store.engine.begin() as conn:
            now = db_now(conn)
            row = conn.execute(select(jobs).where(jobs.c.kind == kind,
                or_(and_(jobs.c.status == 'queued', jobs.c.available_at <= now),
                    and_(jobs.c.status == 'running', jobs.c.lease_until <= now)))
                .order_by(jobs.c.available_at, jobs.c.id).limit(1).with_for_update(skip_locked=True)).mappings().first()
            if not row:
                return None
            if row['attempts'] >= self.max_attempts:
                self._terminal(conn, row, 'failed', error='attempt budget exhausted')
                return None
            value = dict(row)
            value.update(status='running', attempts=row['attempts'] + 1, token=str(uuid.uuid4()),
                lease_until=now + self.lease_seconds, updated_at=now_iso())
            # CAS also protects the SQLite contract-test path, which lacks row locks.
            condition = [jobs.c.id == row['id'], jobs.c.status == row['status'], jobs.c.attempts == row['attempts']]
            result = conn.execute(update(jobs).where(*condition).values(**{k:value[k] for k in
                ['status','attempts','token','lease_until','updated_at']}))
            return value if result.rowcount == 1 else None

    def _owned(self, conn, job, *, lock=False):
        stmt = select(jobs).where(jobs.c.id == job['id'], jobs.c.status == 'running',
            jobs.c.token == job['token'], jobs.c.lease_until > db_now(conn))
        if lock:
            stmt = stmt.with_for_update()
        row = conn.execute(stmt).mappings().first()
        if not row:
            raise LeaseLost('job was cancelled, expired or claimed by another worker')
        return row

    def heartbeat(self, job, progress=None):
        with self.store.engine.begin() as conn:
            self._owned(conn, job, lock=True)
            values = dict(lease_until=db_now(conn) + self.lease_seconds, updated_at=now_iso())
            if progress is not None:
                values['result'] = progress
            conn.execute(update(jobs).where(jobs.c.id == job['id'], jobs.c.token == job['token']).values(**values))

    def _terminal(self, conn, row, status, result=None, error=None):
        conn.execute(update(jobs).where(jobs.c.id == row['id']).values(status=status, result=result,
            error=error, lease_until=0, token=None, updated_at=now_iso()))
        conn.execute(update(queue_state).where(queue_state.c.kind == row['kind']).values(outstanding=queue_state.c.outstanding - 1))
        self.store.audit('job.' + status, row['id'], {'kind': row['kind']}, conn=conn)

    def complete(self, job, result, *, conn=None):
        if conn is not None:
            row = self._owned(conn, job, lock=True)
            self._terminal(conn, row, 'succeeded', result)
        else:
            with self.store.engine.begin() as transaction:
                row = self._owned(transaction, job, lock=True)
                self._terminal(transaction, row, 'succeeded', result)

    def fail(self, job, error_type):
        with self.store.engine.begin() as conn:
            row = self._owned(conn, job, lock=True)
            if row['attempts'] >= self.max_attempts:
                self._terminal(conn, row, 'failed', error=error_type[:128])
            else:
                conn.execute(update(jobs).where(jobs.c.id == job['id']).values(status='queued', token=None,
                    lease_until=0, error=error_type[:128], updated_at=now_iso(),
                    available_at=db_now(conn) + random.uniform(0.1, min(60, 2 ** row['attempts']))))

    def cancel(self, job_id, *, scope):
        with self.store.engine.begin() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id, jobs.c.scope == scope).with_for_update()).mappings().first()
            if not row:
                raise ResourceNotFound(job_id)
            if row['status'] in {'queued', 'running'}:
                self._terminal(conn, row, 'cancelled')
        return self.get(job_id, scope=scope)

    def replay(self, job_id):
        with self.store.engine.begin() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().one()
            state = conn.execute(select(queue_state).where(queue_state.c.kind == row['kind']).with_for_update()).mappings().one()
            if row['status'] != 'failed':
                raise ValueError('only DLQ jobs can be replayed')
            if state['outstanding'] >= self.capacity:
                raise Overloaded('durable queue capacity exceeded')
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(status='queued', attempts=0,
                available_at=db_now(conn), error=None, updated_at=now_iso()))
            conn.execute(update(queue_state).where(queue_state.c.kind == row['kind']).values(outstanding=queue_state.c.outstanding + 1))
            self.store.audit('job.replay', job_id, conn=conn)
