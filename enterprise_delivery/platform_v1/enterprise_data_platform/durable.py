"""Authoritative relational control store. SQLite is for contract tests only."""
from __future__ import annotations
from contextlib import nullcontext

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import (JSON, Boolean, Column, Float, Index, Integer, MetaData, String,
                        Table, Text, UniqueConstraint, and_, delete, func, insert, select, update)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from .catalog import CatalogConflict, CatalogNotFound, CatalogStore
from .context import current_actor, current_context
from .control_models import AgentRegistration, ClientRegistration, GuardrailRule, IndexDeployment
from .control_state import ResourceNotFound
from .models import AccessPolicy, DataProduct
from .policy import PolicyEngine

metadata = MetaData()
document = JSON().with_variant(JSONB(), 'postgresql')
schema_versions = Table('edp_schema_versions', metadata, Column('version', Integer, primary_key=True))
objects = Table('edp_objects', metadata,
    Column('kind', String(64), primary_key=True), Column('id', String(256), primary_key=True),
    Column('revision', Integer, nullable=False), Column('payload', document, nullable=False),
    Column('deleted', Boolean, nullable=False, default=False))
history = Table('edp_object_history', metadata,
    Column('kind', String(64), primary_key=True), Column('id', String(256), primary_key=True),
    Column('revision', Integer, primary_key=True), Column('payload', document, nullable=False),
    Column('deleted', Boolean, nullable=False), Column('created_at', String(40), nullable=False))
audits = Table('edp_audit', metadata,
    Column('id', String(36), primary_key=True), Column('created_at', String(40), nullable=False),
    Column('action', String(128), nullable=False), Column('resource', String(512), nullable=False),
    Column('actor', document, nullable=False), Column('trace_id', String(64)),
    Column('details', document, nullable=False))
Index('edp_audit_time', audits.c.created_at, audits.c.id)
jobs = Table('edp_jobs', metadata,
    Column('id', String(36), primary_key=True), Column('kind', String(32), nullable=False),
    Column('scope', String(64), nullable=False), Column('idempotency', String(128), nullable=False),
    Column('fingerprint', String(64), nullable=False), Column('payload', document, nullable=False),
    Column('status', String(16), nullable=False), Column('result', document),
    Column('attempts', Integer, nullable=False, default=0), Column('available_at', Float, nullable=False),
    Column('lease_until', Float, nullable=False, default=0), Column('token', String(36)),
    Column('created_at', String(40), nullable=False), Column('updated_at', String(40), nullable=False),
    Column('error', String(256)), UniqueConstraint('kind', 'scope', 'idempotency'))
Index('edp_jobs_claim', jobs.c.kind, jobs.c.status, jobs.c.available_at, jobs.c.lease_until)
queue_state = Table('edp_queue_state', metadata, Column('kind', String(32), primary_key=True),
    Column('outstanding', Integer, nullable=False, default=0))
quota_state = Table('edp_quota_state', metadata, Column('scope', String(256), primary_key=True),
    Column('window', Integer, nullable=False, default=0), Column('used', Integer, nullable=False, default=0))
leases = Table('edp_leases', metadata, Column('id', String(36), primary_key=True),
    Column('scope', String(256), primary_key=True), Column('expires_at', Float, nullable=False))
Index('edp_leases_scope_expiry', leases.c.scope, leases.c.expires_at)
records = Table('edp_record_versions', metadata,
    Column('dataset', String(128), primary_key=True), Column('version', String(128), primary_key=True),
    Column('record', String(512), primary_key=True), Column('sequence', String(64), nullable=False),
    Column('event_id', String(256), nullable=False), Column('deleted', Boolean, nullable=False),
    Column('chunk_ids', document, nullable=False), postgresql_partition_by='HASH (dataset)')
checkpoints = Table('edp_checkpoints', metadata,
    Column('dataset', String(128), primary_key=True), Column('version', String(128), primary_key=True),
    Column('partition', String(128), primary_key=True), Column('sequence', String(64), nullable=False))
chunks = Table('edp_chunks', metadata,
    Column('dataset', String(128), primary_key=True), Column('version', String(128), primary_key=True),
    Column('id', String(256), primary_key=True), Column('record', String(512), nullable=False),
    Column('payload', document, nullable=False), postgresql_partition_by='HASH (dataset)')
Index('edp_chunks_record', chunks.c.dataset, chunks.c.version, chunks.c.record)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value):
    def normalize(x):
        if isinstance(x, dict):
            return {k: normalize(v) for k, v in x.items()}
        if isinstance(x, (set, frozenset)):
            return sorted(normalize(v) for v in x)
        if isinstance(x, (list, tuple)):
            return [normalize(v) for v in x]
        return x
    return json.dumps(normalize(value), sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def db_now(conn):
    if conn.dialect.name == 'sqlite':
        return float(conn.scalar(select((func.julianday('now') - 2440587.5) * 86400.0)))
    return float(conn.scalar(select(func.extract('epoch', func.clock_timestamp()))))


class RelationalStore:
    def __init__(self, engine, *, production=True):
        if production and engine.dialect.name != 'postgresql':
            raise ValueError('production control storage requires PostgreSQL')
        self.engine = engine

    def ready(self):
        with self.engine.connect() as conn:
            return conn.scalar(select(func.max(schema_versions.c.version))) == 1

    def audit(self, action, resource, details=None, *, conn=None, actor=None):
        context = current_context.get()
        value = dict(id=str(uuid.uuid4()), created_at=now_iso(), action=action, resource=resource,
            actor=actor or current_actor.get() or {'subject': 'system'}, trace_id=context.trace_id if context else None,
            details=details or {})
        if conn is not None:
            conn.execute(insert(audits).values(**value))
        else:
            with self.engine.begin() as transaction:
                transaction.execute(insert(audits).values(**value))

    def get(self, kind, item_id, *, include_deleted=False):
        with self.engine.connect() as conn:
            row = conn.execute(select(objects).where(objects.c.kind == kind, objects.c.id == item_id)).mappings().first()
        if row is None or (row['deleted'] and not include_deleted):
            raise ResourceNotFound(item_id)
        return dict(row)

    def list(self, kind, *, after='', limit=1000):
        if not 1 <= limit <= 1000:
            raise ValueError('control page limit must be 1..1000')
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(objects).where(objects.c.kind == kind,
                objects.c.id > after, objects.c.deleted.is_(False)).order_by(objects.c.id).limit(limit)).mappings()]

    def put(self, kind, item_id, payload, *, expected_revision=0, deleted=False, conn=None):
        if len(canonical_json(payload).encode()) > 1_000_000:
            raise ValueError('control object exceeds 1 MB')
        revision = expected_revision + 1
        value = dict(kind=kind, id=item_id, revision=revision, payload=payload, deleted=deleted)
        try:
            with (nullcontext(conn) if conn is not None else self.engine.begin()) as conn:
                if expected_revision == 0:
                    conn.execute(insert(objects).values(**value))
                else:
                    result = conn.execute(update(objects).where(objects.c.kind == kind, objects.c.id == item_id,
                        objects.c.revision == expected_revision).values(revision=revision, payload=payload, deleted=deleted))
                    if result.rowcount != 1:
                        raise CatalogConflict('stale control revision')
                conn.execute(insert(history).values(**value, created_at=now_iso()))
                self.audit('control.delete' if deleted else 'control.put', kind + '/' + item_id,
                    {'revision': revision}, conn=conn)
        except IntegrityError as exc:
            raise CatalogConflict('object exists; read its current revision before updating') from exc
        return revision

    def delete(self, kind, item_id, *, expected_revision):
        old = self.get(kind, item_id)
        return self.put(kind, item_id, old['payload'], expected_revision=expected_revision, deleted=True)


T = TypeVar('T', bound=BaseModel)


class SQLRegistry(Generic[T]):
    def __init__(self, store, kind, model):
        self.store, self.kind, self.model = store, kind, model

    def _decode(self, row):
        data = dict(row['payload'])
        if 'revision' in self.model.model_fields:
            data['revision'] = row['revision']
        return self.model.model_validate(data)

    def get(self, item_id):
        return self._decode(self.store.get(self.kind, item_id))

    def list(self, *, after='', limit=1000):
        return [self._decode(row) for row in self.store.list(self.kind, after=after, limit=limit)]

    def put(self, item, *, expected_revision=None):
        revision = getattr(item, 'revision', 0) if expected_revision is None else expected_revision
        new_revision = self.store.put(self.kind, item.id, item.model_dump(mode='json'), expected_revision=revision)
        return item.model_copy(update={'revision': new_revision})

    def delete(self, item_id, *, expected_revision=None):
        if expected_revision is None:
            raise CatalogConflict('expected_revision is required to delete durable control state')
        self.store.delete(self.kind, item_id, expected_revision=expected_revision)


class SQLCatalog(CatalogStore):
    def __init__(self, store):
        self.store = store

    def get(self, dataset_id):
        try:
            return DataProduct.model_validate(self.store.get('datasets', dataset_id)['payload'])
        except ResourceNotFound as exc:
            raise CatalogNotFound(dataset_id) from exc

    def list(self, *, after='', limit=1000):
        return [DataProduct.model_validate(r['payload']) for r in self.store.list('datasets', after=after, limit=limit)]

    def put(self, product, *, expected_version=None):
        product.validate_contract()
        revision = 0
        try:
            old = self.store.get('datasets', product.id, include_deleted=True)
        except ResourceNotFound:
            if expected_version is not None:
                raise CatalogConflict('dataset does not exist for conditional update')
        else:
            if expected_version != old['payload']['version']:
                raise CatalogConflict('expected_version must match the current dataset version')
            if product.version == expected_version:
                raise CatalogConflict('dataset changes require a new version')
            revision = old['revision']
        self.store.put('datasets', product.id, product.model_dump(mode='json'), expected_revision=revision)
        return product.model_copy(deep=True)

    def delete(self, dataset_id, *, expected_version=None):
        old = self.store.get('datasets', dataset_id)
        if old['payload']['version'] != expected_version:
            raise CatalogConflict('expected_version is required')
        self.store.delete('datasets', dataset_id, expected_revision=old['revision'])


class SQLPolicyEngine(PolicyEngine):
    def __init__(self, store):
        self.registry = SQLRegistry(store, 'policies', AccessPolicy)

    def list(self):
        rows = self.registry.list()
        if len(rows) == 1000:
            # Never authorize using an incomplete policy snapshot (e.g. missing
            # deny on the next page). Partition the PDP before this limit.
            raise RuntimeError('policy snapshot exceeds supported bound')
        return sorted(rows, key=lambda p: (p.priority, p.id))

    def put(self, policy):
        return self.registry.put(policy)

    def delete(self, policy_id, *, expected_revision=None):
        return self.registry.delete(policy_id, expected_revision=expected_revision)


class SQLControlState:
    def __init__(self, store):
        self.clients = SQLRegistry(store, 'clients', ClientRegistration)
        self.agents = SQLRegistry(store, 'agents', AgentRegistration)
        self.guardrails = SQLRegistry(store, 'guardrails', GuardrailRule)
        self.indexes = SQLRegistry(store, 'indexes', IndexDeployment)


def migrate(engine):
    """Run separately with DDL credentials before starting API/worker replicas."""
    with engine.begin() as conn:
        if conn.dialect.name == 'postgresql':
            conn.exec_driver_sql('SELECT pg_advisory_xact_lock(726188241)')
        metadata.create_all(conn)
        if conn.dialect.name == 'postgresql':
            for name in ['edp_chunks', 'edp_record_versions']:
                for part in range(32):
                    conn.exec_driver_sql(f'CREATE TABLE IF NOT EXISTS {name}_p{part} PARTITION OF {name} FOR VALUES WITH (MODULUS 32, REMAINDER {part})')
        if conn.dialect.name == 'postgresql':
            conn.exec_driver_sql("CREATE OR REPLACE FUNCTION edp_reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only audit relation'; END; $$")
            for table_name in ['edp_audit', 'edp_object_history']:
                conn.exec_driver_sql(f'DROP TRIGGER IF EXISTS edp_append_only ON {table_name}')
                conn.exec_driver_sql(f'CREATE TRIGGER edp_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table_name} FOR EACH STATEMENT EXECUTE FUNCTION edp_reject_audit_mutation()')
        current = conn.scalar(select(func.max(schema_versions.c.version)))
        if current is None:
            for kind in ['export', 'ingestion']:
                conn.execute(insert(queue_state).values(kind=kind, outstanding=0))
            conn.execute(insert(schema_versions).values(version=1))
        elif current != 1:
            raise RuntimeError('unsupported control schema version')
