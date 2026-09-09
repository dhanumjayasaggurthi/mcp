"""Production composition root; no reference stores, identity or seed data."""
from __future__ import annotations
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import os

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, func, select

from .api import create_app, _register_registry_routes
from .chunks import SQLChunkStore
from .compatibility import AliasRegistration, register_compatibility
from .connectors import ConnectorRouter, SourceRegistration
from .cursor import EncryptedCursorCodec
from .durable import RelationalStore, SQLCatalog, SQLControlState, SQLPolicyEngine, SQLRegistry, audits, jobs, objects, now_iso
from .durable_exports import DurableExportBackend, S3ObjectStorage
from .durable_promotion import SQLIndexPromotionController
from .execution import GovernedService
from .governor import Limits, ResourceGovernor
from .guardrails import GuardrailEngine
from .identity import JWTPrincipalResolver, MountedSecretProvider
from .ingestion import IngestionBridge, IngestionEvent
from .jobs import DurableQueue
from .observability import Metrics, instrument, configure_telemetry, TelemetryProxy
from .planner import GovernedPlanner
from .search import HTTPEmbeddingProvider, OpenSearchBackend, OpenSearchSink, OpenSearchTransport
from .sql_gateway import SQLGateway


class SQLRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_id: str
    sql: str = Field(min_length=1, max_length=20000)
    cursor: str | None = None


class IngestionPage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    events: list[IngestionEvent] = Field(min_length=1, max_length=100)
    stream: str = Field(min_length=1, max_length=128)
    resume_token: dict
    expected_revision: int = Field(ge=0)


def required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(name + ' is required for production startup')
    return value


class SQLOperationsProvider:
    def __init__(self, store):
        self.store = store

    def snapshot(self):
        with self.store.engine.connect() as conn:
            resource_counts = dict(conn.execute(select(objects.c.kind, func.count()).where(objects.c.deleted.is_(False)).group_by(objects.c.kind)).all())
            job_counts = [{'kind': r.kind, 'status': r.status, 'count': r.n} for r in conn.execute(
                select(jobs.c.kind, jobs.c.status, func.count().label('n')).group_by(jobs.c.kind, jobs.c.status))]
            recent = conn.execute(select(audits).order_by(audits.c.created_at.desc(), audits.c.id.desc()).limit(10)).mappings().all()
            def active(kind, field='status', value='active'):
                return conn.scalar(select(func.count()).select_from(objects).where(objects.c.kind == kind,
                    objects.c.deleted.is_(False), objects.c.payload[field].as_string() == value))
            active_datasets, active_clients, healthy_indexes = active('datasets'), active('clients'), active('indexes', 'state', 'healthy')
        return {'generated_at': now_iso(), 'system_status': 'operational', 'environment': 'prod',
            'reference_mode': False,
            'metrics': {'active_data_products': {'value': active_datasets}, 'active_consumers': {'value': active_clients},
                'healthy_indexes': {'value': healthy_indexes}}, 'resources': resource_counts, 'jobs': job_counts,
            'deployment': {'current': os.getenv('EDP_RELEASE', '—'), 'candidate': '—', 'traffic': '—', 'stage': None},
            'policy': {'allowed': active_clients, 'total': resource_counts.get('clients', 0), 'masked_fields': '—',
                'masked_products': '—', 'row_filters': '—', 'quotas_near_limit': '—'},
            'services': [], 'mcp': {'tools': '—', 'resources': '—', 'pending': '—', 'denied': '—'},
            'consumers': [], 'alerts': [], 'recent_mcp': [],
            'audit_events': [{'time': r['created_at'], 'actor': r['actor'].get('subject', 'system'), 'action': r['action'],
                'resource': r['resource'], 'environment': 'prod'} for r in recent]}


@dataclass
class Runtime:
    store: object
    service: object
    catalog: object
    policies: object
    control: object
    router: object
    queue: object
    chunks: object
    object_storage: object
    search: object
    embeddings: object
    embedder: object
    secrets: object
    limits: object
    reranking: object = None

    def close(self):
        self.router.close()
        if self.search:
            self.search.close()
        if self.embeddings:
            self.embeddings.close()
        if self.reranking:
            self.reranking.close()
        if self.object_storage:
            self.object_storage.client.close()
        self.store.engine.dispose()


def control_engine(secrets):
    dsn = secrets.resolve(required('EDP_CONTROL_DSN_REF'))
    from sqlalchemy.engine import make_url
    url = make_url(dsn)
    if url.get_backend_name() != 'postgresql':
        raise ValueError('production control DSN requires PostgreSQL')
    return create_engine(url, pool_size=int(os.getenv('EDP_CONTROL_POOL_SIZE', '10')), max_overflow=0,
        pool_timeout=2, pool_pre_ping=True, pool_recycle=300,
        connect_args={'connect_timeout': 5, 'sslmode': 'verify-full',
            'options': '-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=35000'})


def build_runtime():
    configure_telemetry()
    import boto3
    from botocore.config import Config
    secrets = MountedSecretProvider(os.getenv('EDP_SECRET_DIR', '/run/secrets/edp'))
    engine = control_engine(secrets)
    store = RelationalStore(engine)
    if not store.ready():
        raise RuntimeError('run the control database migrations before startup')
    catalog, policies, control = SQLCatalog(store), SQLPolicyEngine(store), SQLControlState(store)
    limits = Limits(**json.loads(os.getenv('EDP_RESOURCE_LIMITS', '{}')))
    router = ConnectorRouter(store, secrets)
    search = OpenSearchTransport(required('EDP_SEARCH_URL'), secrets.resolve(required('EDP_SEARCH_TOKEN_REF'))) if os.getenv('EDP_SEARCH_URL') else None
    embeddings = OpenSearchTransport(required('EDP_EMBEDDING_URL'), secrets.resolve(required('EDP_EMBEDDING_TOKEN_REF'))) if os.getenv('EDP_EMBEDDING_URL') else None
    embedder = HTTPEmbeddingProvider(embeddings, json.loads(required('EDP_EMBEDDING_PROFILES'))) if embeddings else None
    storage = None
    if os.getenv('EDP_EXPORT_BUCKET'):
        s3 = boto3.client('s3', config=Config(connect_timeout=5, read_timeout=10,
            retries={'max_attempts': 3, 'mode': 'standard'}, max_pool_connections=8))
        storage = S3ObjectStorage(s3, required('EDP_EXPORT_BUCKET'), required('EDP_EXPORT_KMS_KEY_ID'))
    queue = DurableQueue(store, capacity=int(os.getenv('EDP_QUEUE_CAPACITY', '10000')))
    chunks = SQLChunkStore(store)
    exporter = DurableExportBackend(queue, storage, int(os.getenv('EDP_EXPORT_RETENTION_SECONDS', '86400'))) if storage else None
    from .retrieval import HTTPReranker, RetrievalPipeline
    reranking = OpenSearchTransport(required('EDP_RERANK_URL'), secrets.resolve(required('EDP_RERANK_TOKEN_REF'))) if os.getenv('EDP_RERANK_URL') else None
    def load_rules():
        rules = control.guardrails.list()
        if len(rules) == 1000:
            raise RuntimeError('guardrail snapshot exceeds supported bound')
        return rules
    service = GovernedService(store=store, control=control, governor=ResourceGovernor(store, limits),
        planner=GovernedPlanner(os.getenv('EDP_ENGINE_NAME', 'Adaptive Governed Execution Engine')), metrics=Metrics(),
        catalog=catalog, policies=policies,
        cursor_codec=EncryptedCursorCodec(secrets.resolve(required('EDP_CURSOR_SECRET_REF')).encode()),
        structured=TelemetryProxy(router, 'source'), keyword=TelemetryProxy(OpenSearchBackend(search, 'keyword'), 'keyword') if search else None,
        vector=TelemetryProxy(OpenSearchBackend(search, 'vector'), 'vector') if search else None,
        embedder=TelemetryProxy(embedder, 'embedding') if embedder else None, chunks=TelemetryProxy(chunks, 'hydration'), exporter=exporter, guardrails=GuardrailEngine(load_rules),
        retrieval_pipeline=RetrievalPipeline(HTTPReranker(reranking) if reranking else None))
    return Runtime(store, service, catalog, policies, control, router, queue, chunks, storage,
        search, embeddings, embedder, secrets, limits, reranking)


def create_production_app():
    runtime = build_runtime()
    resolver = JWTPrincipalResolver(issuer=required('EDP_OIDC_ISSUER'), audience=required('EDP_OIDC_AUDIENCE'),
        jwks_url=required('EDP_OIDC_JWKS_URL'))
    app = create_app(service=runtime.service, catalog=runtime.catalog, policies=runtime.policies,
        control_state=runtime.control, principal_resolver=resolver,
        control_admin_check=lambda p: 'data-platform-admin' in p.groups and 'edp:admin' in p.attributes.get('oauth_scope', '').split(),
        promotion_controller=SQLIndexPromotionController(runtime.store), operations_provider=SQLOperationsProvider(runtime.store),
        readiness_check=runtime.store.ready)
    from fastapi.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    aliases = SQLRegistry(runtime.store, 'aliases', AliasRegistration)
    register_compatibility(app, runtime.service, aliases, app.state.principal_dependency)
    _register_registry_routes(app, 'sources', runtime.router.registry, SourceRegistration, app.state.admin_dependency)
    _register_registry_routes(app, 'aliases', aliases, AliasRegistration, app.state.admin_dependency)
    gateway = SQLGateway(runtime.service, aliases)
    @app.post('/v1/sql/query')
    def sql(body: SQLRequest, principal=Depends(app.state.principal_dependency)):
        return gateway.execute(principal, body.api_id, body.sql, body.cursor)

    @app.post('/v1/control/ingestion-events', status_code=202)
    def ingest(body: IngestionEvent, principal=Depends(app.state.admin_dependency)):
        result = IngestionBridge(runtime.queue).publish(body)
        return {'job_id': result['id'], 'status': result['status']}

    @app.post('/v1/control/ingestion-pages', status_code=202)
    def ingest_page(body: IngestionPage, principal=Depends(app.state.admin_dependency)):
        return IngestionBridge(runtime.queue).publish_page(body.events, stream=body.stream,
            resume_token=body.resume_token, expected_revision=body.expected_revision)

    @app.get('/v1/control/jobs/{job_id}')
    def job_status(job_id: str, principal=Depends(app.state.admin_dependency)):
        job = runtime.queue.get(job_id)
        return {key: job[key] for key in ['id', 'kind', 'status', 'attempts', 'created_at', 'updated_at', 'result', 'error']}

    @app.post('/v1/control/jobs/{job_id}/replay', status_code=202)
    def replay_job(job_id: str, principal=Depends(app.state.admin_dependency)):
        runtime.queue.replay(job_id)
        return {'job_id': job_id, 'status': 'queued'}

    @asynccontextmanager
    async def lifespan(app):
        yield
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(runtime.close)
    app.router.lifespan_context = lifespan
    app.state.runtime = runtime
    instrument(app)
    return app
