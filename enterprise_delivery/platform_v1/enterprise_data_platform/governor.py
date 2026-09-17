"""Transactional, cross-replica hierarchical admission with no waiting queue."""
from contextlib import contextmanager
from dataclasses import dataclass
import math
import uuid

from sqlalchemy import delete, func, insert, select, update
from .durable import db_now, fingerprint, leases, quota_state


class Overloaded(RuntimeError):
    pass


def ensure_row(conn, table, values):
    if conn.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as upsert
    elif conn.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert as upsert
    else:
        raise ValueError('unsupported authoritative store')
    conn.execute(upsert(table).values(**values).on_conflict_do_nothing())


@dataclass(frozen=True)
class Limits:
    global_concurrency: int = 200
    background_concurrency: int = 40
    tenant_concurrency: int = 40
    client_concurrency: int = 20
    principal_concurrency: int = 10
    source_concurrency: int = 20
    workload_concurrency: int = 40
    requests_per_second: int = 100
    max_seconds: int = 30
    max_result_bytes: int = 8_000_000
    max_export_rows: int = 10_000_000
    max_export_bytes: int = 10_000_000_000

    def __post_init__(self):
        if any(value <= 0 for value in self.__dict__.values()):
            raise ValueError('resource limits must be positive')
        if self.background_concurrency >= self.global_concurrency:
            raise ValueError('background concurrency must reserve interactive capacity')


class ResourceGovernor:
    def __init__(self, store, limits=None):
        self.store, self.limits = store, limits or Limits()

    def scopes(self, principal, source, workload, client=None):
        l = self.limits
        tenant = fingerprint(principal.tenant or '')
        scopes = [('global', l.global_concurrency), ('tenant:' + tenant, l.tenant_concurrency),
            ('client:' + fingerprint([tenant, principal.client_id]), min(l.client_concurrency, client.max_concurrency) if client else l.client_concurrency),
            ('principal:' + fingerprint([tenant, principal.subject, principal.agent_id]), l.principal_concurrency),
            ('source:' + fingerprint(source), l.source_concurrency), ('workload:' + workload, l.workload_concurrency)]
        if workload in {'batch', 'indexing', 'export'}:
            scopes.extend([('background', l.background_concurrency), ('background-tenant:' + tenant, max(1, l.tenant_concurrency // 4)),
                ('background-source:' + fingerprint(source), max(1, l.source_concurrency // 4))])
        return sorted(scopes)

    @contextmanager
    def admit(self, principal, source, workload, context, client=None):
        scope_limits = self.scopes(principal, source, workload, client)
        lease_id = str(uuid.uuid4())
        duration = min(context.remaining(), self.limits.max_seconds)
        with self.store.engine.begin() as conn:
            # Batch the same authoritative checks; retain strict cross-replica limits.
            names = [scope for scope, _ in scope_limits]
            if conn.dialect.name == 'postgresql':
                from sqlalchemy.dialects.postgresql import insert as upsert
            else:
                from sqlalchemy.dialects.sqlite import insert as upsert
            conn.execute(upsert(quota_state).values([dict(scope=n, window=0, used=0) for n in names]).on_conflict_do_nothing())
            rows = {r['scope']: r for r in conn.execute(select(quota_state).where(quota_state.c.scope.in_(names))
                .order_by(quota_state.c.scope).with_for_update()).mappings()}
            now = db_now(conn)
            conn.execute(delete(leases).where(leases.c.scope.in_(names), leases.c.expires_at <= now))
            counts = dict(conn.execute(select(leases.c.scope,func.count()).where(leases.c.scope.in_(names)).group_by(leases.c.scope)).all())
            for scope, cap in scope_limits:
                if counts.get(scope,0) >= cap:
                    raise Overloaded('concurrency budget exhausted')
                if scope.startswith('client:'):
                    row = rows[scope]
                    window = math.floor(now)
                    used = row['used'] if row['window'] == window else 0
                    qps = min(client.rate_limit_rps, self.limits.requests_per_second) if client else self.limits.requests_per_second
                    if used >= qps:
                        raise Overloaded('request rate budget exhausted')
                    conn.execute(update(quota_state).where(quota_state.c.scope == scope).values(window=window,used=used+1))
            conn.execute(insert(leases), [dict(id=lease_id,scope=n,expires_at=now+duration+5) for n in names])

        try:
            context.remaining()
            yield lease_id
        finally:
            with self.store.engine.begin() as conn:
                conn.execute(delete(leases).where(leases.c.id == lease_id))

    def consume(self, scope, limit, seconds=60):
        scope = 'budget:' + fingerprint(scope)
        with self.store.engine.begin() as conn:
            ensure_row(conn, quota_state, dict(scope=scope, window=0, used=0))
            row = conn.execute(select(quota_state).where(quota_state.c.scope == scope).with_for_update()).mappings().one()
            window = int(db_now(conn) // seconds)
            used = row['used'] if row['window'] == window else 0
            if used >= limit:
                raise Overloaded('request budget exhausted')
            conn.execute(update(quota_state).where(quota_state.c.scope == scope).values(window=window, used=used+1))

