"""Connector SPI and capability-specific SQL/REST bindings."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Protocol
import threading

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, inspect, select, text

from .backends import StructuredBackend, StructuredPage
from .durable import SQLRegistry
from .models import CountMode
from .resilience import CircuitBreaker


@dataclass(frozen=True)
class ConnectorCapabilities:
    predicate: bool = True
    projection: bool = True
    ordering: bool = True
    keyset: bool = True
    aggregation: bool = False
    statistics: bool = False
    partitions: bool = False
    bulk: bool = True
    cdc: bool = False
    cancellation: bool = False
    statement_timeout: bool = False
    plan_inspection: bool = False
    native_nulls_last: bool = True


@dataclass(frozen=True)
class SourceStatistics:
    estimated_rows: int | None = None
    estimated_scan_rows: int | None = None
    indexed_fields: tuple[str, ...] = ()
    stale: bool = True


class Connector(Protocol):
    capabilities: ConnectorCapabilities
    def query(self, **kwargs) -> StructuredPage: ...
    def aggregate(self, **kwargs) -> list[dict]: ...
    def schema(self, product) -> list[dict]: ...
    def statistics(self, product, filter_expr=None) -> SourceStatistics: ...
    def partitions(self, product) -> list[dict]: ...
    def bulk_read(self, **kwargs): ...
    def changes(self, product, checkpoint): ...
    def health(self) -> bool: ...
    def close(self) -> None: ...


class SecretProvider(Protocol):
    def resolve(self, reference: str) -> str: ...


class SourceRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9._-]{1,128}$')
    revision: int = Field(default=0, ge=0)
    kind: str
    secret_ref: str = Field(pattern=r'^(file|vault|aws-sm|env)://[^\s]+$')
    enabled: bool = True
    pool_size: int = Field(default=8, ge=1, le=100)
    pool_timeout_seconds: int = Field(default=2, ge=1, le=30)
    statement_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_scan_rows: int = Field(default=1_000_000, ge=1)
    native_scan_governor: bool = False
    endpoint: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list, max_length=10)
    options: dict = Field(default_factory=dict)


class SQLConnector(StructuredBackend):
    def __init__(self, engine, capabilities, timeout_seconds=30, max_scan_rows=None, allow_unknown_cost=False):
        from .sqlalchemy_backend import SQLAlchemyStructuredBackend
        self.engine, self.capabilities = engine, capabilities
        self.backend = SQLAlchemyStructuredBackend(lambda _: engine, timeout_seconds=timeout_seconds,
            native_nulls_last=capabilities.native_nulls_last, max_scan_rows=max_scan_rows,
            allow_unknown_cost=allow_unknown_cost)
        self.breaker = CircuitBreaker()

    def query(self, **kwargs):
        # Retry only reads, with the original request deadline.
        from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout
        return self.breaker.call(lambda: self.backend.query(**kwargs), retryable=(OperationalError, PoolTimeout), attempts=2)

    def schema(self, product):
        return inspect(self.engine).get_columns(product.source.object_name, schema=product.source.schema_name)

    def aggregate(self, **kwargs):
        if not self.capabilities.aggregation:
            raise ValueError('source does not support aggregate pushdown')
        return self.backend.aggregate(**kwargs)

    def statistics(self, product, filter_expr=None):
        if self.engine.dialect.name != 'postgresql':
            return SourceStatistics()
        from sqlalchemy import func
        with self.engine.connect() as conn:
            # reltuples is cheap, approximate, and never presented as a filtered
            # or tenant-specific count. Planner estimate only.
            value = conn.execute(text('SELECT c.reltuples FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
                'WHERE c.relname=:name AND n.nspname=COALESCE(:schema, current_schema())'),
                {'name': product.source.object_name, 'schema': product.source.schema_name}).scalar()
        return SourceStatistics(estimated_rows=max(0, int(value)) if value is not None else None)

    def partitions(self, product):
        return []  # Explicit source-provided disjoint partitions required.

    def bulk_read(self, **kwargs):
        position = kwargs.pop('position', None)
        while True:
            page = self.query(**kwargs, position=position, count_mode=CountMode.NONE)
            yield page.rows
            if not page.next_position:
                break
            position = page.next_position

    def changes(self, product, checkpoint):
        raise NotImplementedError('use a source CDC plugin or durable event bridge')

    def health(self):
        with self.engine.connect() as conn:
            return conn.scalar(select(1)) == 1

    def close(self):
        self.engine.dispose()


def sql_factory(kind, registration, secrets):
    from sqlalchemy.engine import make_url
    url = make_url(secrets.resolve(registration.secret_ref))
    expected = {'postgres': {'postgresql'}, 'mysql': {'mysql'}, 'mariadb': {'mariadb', 'mysql'},
        'snowflake': {'snowflake'}, 'denodo': {'denodo'}, 'sqlalchemy': {url.get_backend_name()}}
    if url.get_backend_name() not in expected[kind]:
        raise ValueError('source kind and installed dialect do not match')
    connect_args = {}
    if kind == 'postgres':
        connect_args = {'connect_timeout': registration.pool_timeout_seconds,
            'options': f'-c statement_timeout={registration.statement_timeout_seconds * 1000} -c default_transaction_read_only=on',
            'sslmode': 'verify-full'}
    elif kind in {'mysql', 'mariadb'}:
        if url.get_driver_name() != 'pymysql':
            raise ValueError('MySQL/MariaDB adapter requires the PyMySQL driver; register other drivers as validated plugins')
        if kind == 'mariadb':
            url = url.set(drivername='mariadb+pymysql')
        connect_args = {'connect_timeout': registration.pool_timeout_seconds,
            'read_timeout': registration.statement_timeout_seconds, 'write_timeout': registration.statement_timeout_seconds,
            'ssl_verify_cert': True, 'ssl_verify_identity': True}
    elif kind == 'snowflake':
        connect_args = {'login_timeout': registration.pool_timeout_seconds,
            'network_timeout': registration.statement_timeout_seconds,
            'session_parameters': {'STATEMENT_TIMEOUT_IN_SECONDS': registration.statement_timeout_seconds}}
    # Generic/Denodo/ODBC use the driver's validated URL configuration. No
    # arbitrary options are expanded into code or connection keyword arguments.
    engine = create_engine(url, pool_size=registration.pool_size, max_overflow=0,
        pool_timeout=registration.pool_timeout_seconds, pool_pre_ping=True, pool_recycle=300, connect_args=connect_args)
    caps = ConnectorCapabilities(aggregation=True, statistics=kind == 'postgres', plan_inspection=kind == 'postgres',
        statement_timeout=kind in {'postgres', 'mysql', 'mariadb', 'snowflake'}, native_nulls_last=engine.dialect.name not in {'mysql', 'mariadb', 'mssql'})
    return SQLConnector(engine, caps, registration.statement_timeout_seconds, registration.max_scan_rows,
        registration.native_scan_governor)


class ConnectorRouter(StructuredBackend):
    """Bounded process-local connection handles; registrations stay in SQL."""
    def __init__(self, store, secrets, factories=None, max_sources=128):
        self.registry = SQLRegistry(store, 'sources', SourceRegistration)
        self.secrets, self.max_sources = secrets, max_sources
        self.factories = {kind: (lambda r, s, kind=kind: sql_factory(kind, r, s))
            for kind in ['postgres', 'mysql', 'mariadb', 'snowflake', 'denodo', 'sqlalchemy']}
        from .rest_connector import RESTConnector
        self.factories['rest'] = RESTConnector
        self.factories.update(factories or {})
        self._handles = {}
        self._lock = threading.RLock()

    def resolve(self, product):
        source_id = product.source.source_id or product.source.connector
        registration = self.registry.get(source_id)
        if not registration.enabled:
            raise RuntimeError('source is disabled')
        with self._lock:
            old = self._handles.get(source_id)
            if old and old[0] == registration.revision:
                return old[1]
            if old:
                old[1].close()
                del self._handles[source_id]
            if len(self._handles) >= self.max_sources:
                raise RuntimeError('source pool capacity exceeded')
            if registration.kind not in self.factories:
                raise ValueError('connector kind is not installed')
            connector = self.factories[registration.kind](registration, self.secrets)
            self._handles[source_id] = (registration.revision, connector)
            return connector

    def query(self, **kwargs):
        return self.resolve(kwargs['product']).query(**kwargs)

    def aggregate(self, **kwargs):
        return self.resolve(kwargs['product']).aggregate(**kwargs)

    def close(self):
        with self._lock:
            for _, connector in self._handles.values():
                connector.close()
            self._handles.clear()
