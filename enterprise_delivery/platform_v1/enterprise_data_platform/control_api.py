"""Control Hub contracts. Every administrative route shares the admin gate."""
from __future__ import annotations
import base64
import json
from typing import Literal
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, tuple_
from .models import AccessPolicy, Capability, DataProduct, Principal, RetrieveRequest
from .control_models import AgentRegistration, ClientRegistration, GuardrailRule, IndexDeployment
from .connectors import SourceRegistration
from .durable import SQLRegistry, audits, history, jobs
from .services import AccessDenied
from .context import execution_context, trace_id


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: str
    operation: Capability
    principal: Principal


class Collection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9._-]{1,127}$')
    revision: int = Field(default=0, ge=0)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = ''
    status: Literal['draft', 'active', 'disabled'] = 'draft'
    members: list[str] = Field(min_length=1, max_length=7)
    max_top_k: int = Field(default=20, ge=1, le=100)


def encode_cursor(values):
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode()


def decode_cursor(value):
    try:
        data = json.loads(base64.b64decode(value, altchars=b'-_', validate=True))
        if not isinstance(data, list) or len(data) != 2 or not all(isinstance(v, str) and len(v) <= 64 for v in data):
            raise ValueError()
        return data
    except Exception as exc:
        raise HTTPException(400, 'invalid page cursor') from exc


def register_control_api(app, service, catalog, policies, control):
    admin = app.state.admin_dependency
    principal_dep = app.state.principal_dependency
    store = getattr(service, 'store', None)

    @app.get('/v1/control/forms')
    def forms(_=Depends(admin)):
        models = {'datasets': DataProduct, 'policies': AccessPolicy, 'sources': SourceRegistration,
                  'clients': ClientRegistration, 'agents': AgentRegistration, 'guardrails': GuardrailRule,
                  'indexes': IndexDeployment, 'collections': Collection}
        return {name: model.model_json_schema() for name, model in models.items()}

    @app.get('/v1/control/session')
    def session(principal=Depends(admin)):
        return {'subject': principal.subject, 'client_id': principal.client_id,
                'environment': __import__('os').getenv('EDP_ENVIRONMENT', 'prod'),
                'mcp_endpoint': '/mcp' if getattr(app.state, 'mcp_manager', None) else None,
                'durable': store is not None}

    @app.post('/v1/control/simulate')
    def simulate(body: SimulationRequest, _=Depends(admin)):
        # Uses exactly the same identity/registration/policy gate as execution;
        # the supplied identity is hypothetical and never becomes a session.
        try:
            _, decision = service._decision(body.principal, body.dataset_id, body.operation)
            result = decision.model_dump(mode='json')
        except AccessDenied as exc:
            result = {'allowed': False, 'reason': str(exc), 'allowed_fields': [], 'masked_fields': [],
                      'mandatory_filter': None, 'max_limit': 0, 'max_top_k': 0}
        if store:
            store.audit('policy.simulate', body.dataset_id, {'operation': body.operation.value, 'allowed': result['allowed']})
        return {**result, 'simulation': True, 'trace_id': trace_id()}

    def require_store():
        if store is None:
            raise HTTPException(503, 'durable control storage is not configured')
        return store

    @app.get('/v1/control/audit')
    def audit_page(cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                   action: str | None = None, resource: str | None = None, actor: str | None = None,
                   trace: str | None = None, since: str | None = None, until: str | None = None, _=Depends(admin)):
        stmt = select(audits)
        for column, value in [(audits.c.action, action), (audits.c.resource, resource), (audits.c.trace_id, trace)]:
            if value: stmt = stmt.where(column == value)
        if actor: stmt = stmt.where(audits.c.actor['subject'].as_string() == actor)
        if since: stmt = stmt.where(audits.c.created_at >= since)
        if until: stmt = stmt.where(audits.c.created_at <= until)
        if cursor: stmt = stmt.where(tuple_(audits.c.created_at, audits.c.id) < tuple_(*decode_cursor(cursor)))
        with require_store().engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(stmt.order_by(audits.c.created_at.desc(), audits.c.id.desc()).limit(limit+1)).mappings()]
        return {'events': rows[:limit], 'next_cursor': encode_cursor([rows[limit-1]['created_at'], rows[limit-1]['id']]) if len(rows) > limit else None}

    @app.get('/v1/control/jobs')
    def job_page(cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                 status: str | None = None, kind: str | None = None, _=Depends(admin)):
        # Payloads contain caller grants and are deliberately excluded.
        columns = [jobs.c[k] for k in ['id','kind','status','attempts','created_at','updated_at','result','error','lease_until']]
        stmt = select(*columns)
        if status: stmt = stmt.where(jobs.c.status == status)
        if kind: stmt = stmt.where(jobs.c.kind == kind)
        if cursor: stmt = stmt.where(tuple_(jobs.c.created_at,jobs.c.id) < tuple_(*decode_cursor(cursor)))
        with require_store().engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(stmt.order_by(jobs.c.created_at.desc(),jobs.c.id.desc()).limit(limit+1)).mappings()]
        return {'jobs': rows[:limit], 'next_cursor': encode_cursor([rows[limit-1]['created_at'], rows[limit-1]['id']]) if len(rows)>limit else None}

    @app.get('/v1/control/history/{kind}/{item_id}')
    def revisions(kind: str, item_id: str, before: int | None = None,
                  limit: int = Query(20, ge=1, le=100), _=Depends(admin)):
        stmt = select(history).where(history.c.kind==kind, history.c.id==item_id)
        if before is not None: stmt = stmt.where(history.c.revision < before)
        with require_store().engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(stmt.order_by(history.c.revision.desc()).limit(limit+1)).mappings()]
        return {'history': rows[:limit], 'next_before': rows[limit-1]['revision'] if len(rows)>limit else None}

    if store:
        from .api import _register_registry_routes
        collections = SQLRegistry(store, 'collections', Collection)
        _register_registry_routes(app, 'collections', collections, Collection, admin)

        @app.post('/v1/collections/{collection_id}/retrieve')
        def retrieve_collection(collection_id: str, body: RetrieveRequest, principal=Depends(principal_dep)):
            collection = collections.get(collection_id)
            if collection.status != 'active': raise AccessDenied('collection is not active')
            if len(set(collection.members)) != len(collection.members): raise ValueError('collection members must be unique')
            with execution_context():
                # Preflight every member before touching a source, never partial grants.
                for member in collection.members:
                    product, _ = service._decision(principal, member, Capability.RETRIEVE)
                    service.resolve_retrieval_mode(product, body.mode)
                top_k = min(body.top_k, collection.max_top_k)
                results = []
                members = []
                for member in collection.members:
                    response = service.retrieve(principal, member, body.model_copy(update={'top_k': top_k}))
                    members.append({'dataset_id': member, 'returned': len(response.results), 'trace_id': response.trace_id})
                    for rank, hit in enumerate(response.results, 1):
                        item = hit.model_dump(mode='json')
                        item['score'] = 1 / (60 + rank)
                        item['source'] = {**item['source'], 'dataset': member}
                        results.append(item)
                results.sort(key=lambda x: (-x['score'], x['source']['dataset'], x.get('chunk_id') or x['record_id']))
                return {'collection_id': collection_id, 'score_kind':'member_rank_rrf', 'members':members,
                        'results':results[:top_k], 'trace_id':trace_id()}
