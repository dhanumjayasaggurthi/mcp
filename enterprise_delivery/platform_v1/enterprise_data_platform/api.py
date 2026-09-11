from __future__ import annotations

from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from .catalog import CatalogConflict, CatalogNotFound, CatalogStore
from .control_models import AgentRegistration, ClientRegistration, ControlOverview, GuardrailRule, IndexDeployment, ManagedStatus
from .control_state import ControlState, ResourceNotFound
from .cursor import CursorError
from .models import (
    AccessPolicy,
    ErrorResponse,
    LookupRequest,
    ExportJob,
    ExportRequest,
    Principal,
    RetrievalResponse,
    RetrievalContractResponse,
    RetrieveRequest,
    SearchRequest,
    StructuredQueryRequest,
    StructuredQueryResponse,
    VectorSearchRequest,
)
from .policy import PolicyEngine
from .query_validation import QueryValidationError
from .services import AccessDenied, CapabilityUnavailable, PlatformService
from .aggregation import AggregateRequest
from .promotion import IndexPromotionController, RetrievalQualityEvidence, PromotionBlocked
from .operations import InMemoryOperationsProvider, OperationsProvider
from .branding import API_TITLE

PrincipalResolver = Callable[[Request], Principal]
ControlAdminCheck = Callable[[Principal], bool]


def create_app(
    *,
    service: PlatformService,
    catalog: CatalogStore,
    policies: PolicyEngine,
    control_state: ControlState,
    principal_resolver: PrincipalResolver,
    control_admin_check: ControlAdminCheck,
    promotion_controller: IndexPromotionController | None = None,
    operations_provider: OperationsProvider | None = None,
    readiness_check=None,
) -> FastAPI:
    """Create the v1 API application.

    Authentication is deliberately injected. There is no insecure default header
    identity adapter; production must wire OIDC/workload identity/mTLS at the
    gateway or via a resolver supplied by the host application.
    """

    error_responses = {
        code: {"model": ErrorResponse, "description": description}
        for code, description in {
            400: "Invalid request semantics",
            401: "Missing or invalid identity",
            403: "Operation is not authorized",
            404: "Dataset or resource not found",
            409: "Version or idempotency conflict",
            413: "Request body exceeds the configured budget",
            422: "Request does not match the JSON schema",
            429: "Capacity or rate limit reached",
            503: "A required backend is unavailable",
            504: "Request deadline exceeded",
        }.items()
    }
    app = FastAPI(
        title=API_TITLE,
        version="1.0.0",
        description="Governed structured, retrieval, MCP and agent-facing APIs for registered RDH data products.",
        responses=error_responses,
    )
    bearer_scheme = HTTPBearer(
        auto_error=False,
        description="Environment-issued OAuth 2.0 bearer access token.",
    )
    promotion_controller = promotion_controller or IndexPromotionController()
    operations_provider = operations_provider or InMemoryOperationsProvider()

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        result = _json_error(exc.status_code, exc.detail)
        if exc.headers:
            result.headers.update(exc.headers)
        return result

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        # Preserve the 422 contract without reflecting submitted values or bodies.
        return _json_error(422, 'request validation failed')

    async def principal_dep(
        request: Request,
        _: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> Principal:
        from starlette.concurrency import run_in_threadpool
        from .context import current_actor
        try:
            principal = await run_in_threadpool(principal_resolver, request)
        except HTTPException:
            if hasattr(service, 'store'):
                await run_in_threadpool(service.store.audit, 'authentication.deny', 'api')
            raise
        current_actor.set({'subject': principal.subject, 'client_id': principal.client_id,
            'tenant': principal.tenant, 'agent_id': principal.agent_id, 'groups': sorted(principal.groups)})
        if hasattr(service, 'store'):
            await run_in_threadpool(service.store.audit, 'authentication.allow', 'api')
        return principal

    def admin_dep(principal: Principal = Depends(principal_dep)) -> Principal:
        if not control_admin_check(principal):
            if hasattr(service, 'store'):
                service.store.audit('administration.deny', 'control')
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="control hub administrator permission required")
        return principal

    @app.exception_handler(AccessDenied)
    async def access_denied_handler(_: Request, exc: AccessDenied):
        return _json_error(status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(CatalogNotFound)
    async def catalog_not_found_handler(_: Request, exc: CatalogNotFound):
        return _json_error(status.HTTP_404_NOT_FOUND, f"dataset not found: {exc.args[0]}")

    @app.exception_handler(ResourceNotFound)
    async def resource_not_found_handler(_: Request, exc: ResourceNotFound):
        return _json_error(status.HTTP_404_NOT_FOUND, f"resource not found: {exc.args[0]}")

    async def bad_request_handler(_: Request, exc: Exception):
        return _json_error(status.HTTP_400_BAD_REQUEST, str(exc))

    app.add_exception_handler(QueryValidationError, bad_request_handler)
    app.add_exception_handler(CursorError, bad_request_handler)
    app.add_exception_handler(ValueError, bad_request_handler)

    @app.exception_handler(CapabilityUnavailable)
    async def capability_unavailable_handler(_: Request, exc: CapabilityUnavailable):
        return _json_error(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    @app.exception_handler(CatalogConflict)
    async def conflict_handler(_: Request, exc: CatalogConflict):
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(PromotionBlocked)
    async def promotion_blocked_handler(_: Request, exc: PromotionBlocked):
        return _json_error(status.HTTP_409_CONFLICT, str(exc))

    from .guardrails import GuardrailViolation
    from .governor import Overloaded
    from .resilience import BackendUnavailable
    from .jobs import LeaseLost
    from sqlalchemy.exc import SQLAlchemyError
    from .observability import RequestBoundary
    app.add_middleware(RequestBoundary)
    app.add_exception_handler(GuardrailViolation, access_denied_handler)

    @app.exception_handler(Overloaded)
    async def overload_handler(request, exc):
        result = _json_error(429, str(exc))
        result.headers['Retry-After'] = '1'
        result.headers['RateLimit-Remaining'] = '0'
        return result

    async def unavailable_handler(request, exc):
        return _json_error(503, 'a required backend is unavailable')
    app.add_exception_handler(SQLAlchemyError, unavailable_handler)
    app.add_exception_handler(BackendUnavailable, unavailable_handler)
    app.add_exception_handler(LeaseLost, conflict_handler)

    @app.exception_handler(TimeoutError)
    async def timeout_handler(request, exc):
        return _json_error(504, 'request deadline exceeded')

    @app.get('/livez')
    def live():
        return {'status': 'alive'}

    @app.get('/readyz')
    def ready():
        if readiness_check and not readiness_check():
            return _json_error(503, 'control store is not ready')
        return {'status': 'ready'}

    def visible_decision(principal, product, capability):
        from .models import PolicyDecision
        try:
            return service._decision(principal, product.id, capability)[1]
        except AccessDenied:
            return PolicyDecision(allowed=False, reason='not authorized')

    @app.get("/v1/health")
    def health():
        if readiness_check and not readiness_check():
            raise HTTPException(status_code=503, detail='control plane is not ready')
        return {"status": "healthy", "api_version": "v1"}

    @app.post('/v1/datasets/{dataset_id}/aggregate', response_model=StructuredQueryResponse)
    def aggregate(dataset_id: str, body: AggregateRequest, principal: Principal = Depends(principal_dep)):
        return service.aggregate(principal, dataset_id, body)

    @app.get("/v1/datasets")
    def list_datasets(principal: Principal = Depends(principal_dep)):
        visible = []
        for product in catalog.list():
            # Discovery does not leak inaccessible products. A product is visible
            # if the principal has any matching allow policy for discover or one
            # of its enabled data capabilities.
            for capability in product.capabilities:
                decision = visible_decision(principal, product, capability)
                if decision.allowed:
                    visible.append(
                        {
                            "id": product.id,
                            "display_name": product.display_name,
                            "description": product.description,
                            "version": product.version,
                            "status": product.status,
                            "capabilities": sorted(c.value for c in product.capabilities),
                        }
                    )
                    break
        return {"datasets": visible}

    @app.get("/v1/datasets/{dataset_id}/schema")
    def schema(dataset_id: str, principal: Principal = Depends(principal_dep)):
        product = catalog.get(dataset_id)
        # Use QUERY as the discovery authorization fallback; if unavailable, any
        # enabled retrieval capability can authorize schema visibility.
        decisions = [visible_decision(principal, product, c) for c in product.capabilities]
        allowed = [d for d in decisions if d.allowed]
        if not allowed:
            raise AccessDenied("schema access denied")
        fields = set().union(*(d.allowed_fields for d in allowed))
        return {
            "dataset_id": product.id,
            "version": product.version,
            "identity_fields": [f for f in product.identity_fields if f in fields],
            "fields": [f.model_dump() for f in product.fields if f.name in fields],
        }

    @app.get("/v1/datasets/{dataset_id}/capabilities")
    def capabilities(dataset_id: str, principal: Principal = Depends(principal_dep)):
        product = catalog.get(dataset_id)
        enabled = []
        for capability in product.capabilities:
            decision = visible_decision(principal, product, capability)
            if decision.allowed:
                enabled.append(capability.value)
        return {"dataset_id": dataset_id, "capabilities": sorted(enabled)}

    @app.get(
        "/v1/datasets/{dataset_id}/retrieval-contract",
        response_model=RetrievalContractResponse,
    )
    def retrieval_contract(dataset_id: str, principal: Principal = Depends(principal_dep)):
        """Return the caller-specific, safe contract required by retrieval clients."""
        from .chunks import retrieval_version
        from .models import Capability

        product = catalog.get(dataset_id)
        endpoints = {
            Capability.KEYWORD: f"/v1/datasets/{dataset_id}/search/keyword",
            Capability.VECTOR: f"/v1/datasets/{dataset_id}/search/vector",
            Capability.HYBRID: f"/v1/datasets/{dataset_id}/search/hybrid",
            Capability.RETRIEVE: f"/v1/datasets/{dataset_id}/retrieve",
        }
        operations = {}
        decisions = {}
        for capability, endpoint in endpoints.items():
            if capability not in product.capabilities:
                continue
            decision = visible_decision(principal, product, capability)
            if not decision.allowed:
                continue
            decisions[capability] = decision
            citation_labels = []
            if product.retrieval and product.retrieval.backend == "postgres" and product.retrieval.postgres:
                citation_labels = sorted(
                    label
                    for label, field in product.retrieval.postgres.citation_fields.items()
                    if field in decision.allowed_fields - decision.masked_fields
                )
            operations[capability.value] = {
                "endpoint": endpoint,
                "oauth_scope": f"edp:{capability.value}",
                "max_top_k": decision.max_top_k,
                "filters_url": f"/v1/datasets/{dataset_id}/filters?operation={capability.value}",
                "citation_labels": citation_labels,
            }
        if not operations:
            raise AccessDenied("retrieval contract access denied")

        vector = None
        retrieval = product.retrieval
        # Direct embedding inputs belong to the vector-search contract. Hybrid
        # and retrieve accept query strings and keep their embedding details
        # server-side.
        vector_visible = Capability.VECTOR in decisions
        if vector_visible and retrieval and retrieval.vector:
            embedder_models = getattr(service.embedder, "models", None)
            query_text_supported = service.embedder is not None and (
                embedder_models is None
                or retrieval.vector.profile_id in embedder_models
            )
            vector = {
                "profile_id": retrieval.vector.profile_id,
                "dimensions": retrieval.vector.dimensions,
                "distance": retrieval.vector.distance,
                "accepted_inputs": ["vector"] + (["query_text"] if query_text_supported else []),
                "query_text_supported": query_text_supported,
            }
        return {
            "dataset_id": product.id,
            "dataset_version": product.version,
            "index_version": retrieval_version(product),
            "operations": operations,
            "vector": vector,
            "result_identity": ["record_id", "chunk_id"],
            "pagination": "bounded_top_k",
            "scores": {
                "route_local": True,
                "comparable_across_modes": False,
                "score_kind_reported_per_response": True,
                "ranks_reported_per_mode": True,
            },
        }

    @app.get("/v1/datasets/{dataset_id}/filters")
    def filters(dataset_id: str, operation: str = "query", principal: Principal = Depends(principal_dep)):
        from .models import Capability
        from .filter_contract import filter_contract
        if operation not in {"query", "keyword", "vector", "hybrid", "retrieve"}:
            raise ValueError("invalid filter operation")
        product, decision = service._decision(principal, dataset_id, Capability(operation))
        return filter_contract(product, decision, operation)

    @app.post("/v1/datasets/{dataset_id}/records/lookup", response_model=StructuredQueryResponse)
    def lookup(dataset_id: str, body: LookupRequest, principal: Principal = Depends(principal_dep)):
        from .models import Capability
        from .policy import and_filters
        product, _ = service._decision(principal, dataset_id, Capability.QUERY)
        field = body.id_field
        if field is None:
            if product.retrieval and product.retrieval.backend == "postgres":
                field = product.retrieval.postgres.record_id_field
            elif len(product.identity_fields) == 1:
                field = product.identity_fields[0]
            else:
                raise ValueError("choose id_field or use /query for composite identities")
        return service.query(principal, dataset_id, StructuredQueryRequest(
            select=body.select, filter=and_filters(body.filter, {"field": field, "op": "in", "value": body.ids}),
            order_by=body.order_by, limit=body.limit, cursor=body.cursor))

    @app.post("/v1/datasets/{dataset_id}/query", response_model=StructuredQueryResponse)
    def query(dataset_id: str, body: StructuredQueryRequest, principal: Principal = Depends(principal_dep)):
        return service.query(principal, dataset_id, body)

    @app.post("/v1/datasets/{dataset_id}/search/keyword", response_model=RetrievalResponse)
    def keyword(dataset_id: str, body: SearchRequest, principal: Principal = Depends(principal_dep)):
        return service.keyword_search(principal, dataset_id, body)

    @app.post("/v1/datasets/{dataset_id}/search/vector", response_model=RetrievalResponse)
    def vector(dataset_id: str, body: VectorSearchRequest, principal: Principal = Depends(principal_dep)):
        return service.vector_search(principal, dataset_id, body)

    @app.post("/v1/datasets/{dataset_id}/search/hybrid", response_model=RetrievalResponse)
    def hybrid(dataset_id: str, body: SearchRequest, principal: Principal = Depends(principal_dep)):
        return service.hybrid_search(principal, dataset_id, body)

    @app.post("/v1/datasets/{dataset_id}/retrieve", response_model=RetrievalResponse)
    def retrieve(dataset_id: str, body: RetrieveRequest, principal: Principal = Depends(principal_dep)):
        return service.retrieve(principal, dataset_id, body)

    @app.post("/v1/datasets/{dataset_id}/exports", response_model=ExportJob, status_code=status.HTTP_202_ACCEPTED)
    def export(dataset_id: str, body: ExportRequest, principal: Principal = Depends(principal_dep)):
        return service.export(principal, dataset_id, body)

    # ---------------------------- Control Hub ----------------------------
    @app.get("/v1/control/overview", response_model=ControlOverview)
    def control_overview(_: Principal = Depends(admin_dep)):
        indexes = control_state.indexes.list()
        clients = control_state.clients.list()
        agents = control_state.agents.list()
        degraded = sum(1 for i in indexes if i.state in {"degraded", "failed"})
        return ControlOverview(
            datasets=len(catalog.list()),
            policies=len(policies.list()),
            clients=len(clients),
            agents=len(agents),
            guardrails=len(control_state.guardrails.list()),
            indexes=len(indexes),
            healthy_indexes=sum(1 for i in indexes if i.state == "healthy"),
            degraded_indexes=degraded,
            active_clients=sum(1 for c in clients if c.status == ManagedStatus.ACTIVE),
            active_agents=sum(1 for a in agents if a.status == ManagedStatus.ACTIVE),
            control_plane_status="degraded" if degraded else "healthy",
        )

    @app.get("/v1/control/dashboard")
    def control_dashboard(_: Principal = Depends(admin_dep)):
        return operations_provider.snapshot()

    @app.get("/v1/control/datasets")
    def control_datasets(_: Principal = Depends(admin_dep)):
        return {"datasets": [p.model_dump(mode="json") for p in catalog.list()]}

    @app.put("/v1/control/datasets/{dataset_id}")
    def put_dataset(dataset_id: str, body: dict, response: Response, expected_version: str | None = None, _: Principal = Depends(admin_dep)):
        from .models import DataProduct

        product = DataProduct.model_validate({**body, "id": dataset_id})
        if getattr(app.state, "dataset_validator", None):
            app.state.dataset_validator(product)
        saved = catalog.put(product, expected_version=expected_version)
        response.headers["ETag"] = saved.version
        return saved.model_dump(mode="json")

    @app.get("/v1/control/policies")
    def list_policies(_: Principal = Depends(admin_dep)):
        return {"policies": [p.model_dump(mode="json") for p in policies.list()]}

    @app.delete('/v1/control/datasets/{dataset_id}', status_code=204)
    def delete_dataset(dataset_id: str, expected_version: str | None = None, _: Principal = Depends(admin_dep)):
        catalog.delete(dataset_id, expected_version=expected_version)

    @app.put("/v1/control/policies/{policy_id}")
    def put_policy(policy_id: str, body: dict, _: Principal = Depends(admin_dep)):
        policy = AccessPolicy.model_validate({**body, "id": policy_id})
        saved = policies.put(policy) or policy
        return saved.model_dump(mode="json")

    @app.delete("/v1/control/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_policy(policy_id: str, expected_revision: int | None = None, _: Principal = Depends(admin_dep)):
        if hasattr(policies, 'registry'):
            policies.delete(policy_id, expected_revision=expected_revision)
        else:
            policies.delete(policy_id)

    _register_registry_routes(app, "clients", control_state.clients, ClientRegistration, admin_dep)
    _register_registry_routes(app, "agents", control_state.agents, AgentRegistration, admin_dep)
    _register_registry_routes(app, "guardrails", control_state.guardrails, GuardrailRule, admin_dep)
    _register_registry_routes(app, "indexes", control_state.indexes, IndexDeployment, admin_dep)

    @app.post("/v1/control/indexes/{index_id}/validate")
    def validate_index(index_id: str, body: dict, _: Principal = Depends(admin_dep)):
        if hasattr(promotion_controller, 'transition'):
            return promotion_controller.transition(index_id, 'validate', body).model_dump(mode='json')
        deployment = control_state.indexes.get(index_id)
        evidence = RetrievalQualityEvidence(**body)
        updated = promotion_controller.validate_candidate(deployment, evidence)
        return control_state.indexes.put(updated).model_dump(mode="json")

    @app.post("/v1/control/indexes/{index_id}/canary")
    def canary_index(index_id: str, body: dict, _: Principal = Depends(admin_dep)):
        if hasattr(promotion_controller, 'transition'):
            return promotion_controller.transition(index_id, 'canary', body).model_dump(mode='json')
        deployment = control_state.indexes.get(index_id)
        updated = promotion_controller.set_canary(deployment, int(body.get("percent", -1)))
        return control_state.indexes.put(updated).model_dump(mode="json")

    @app.post("/v1/control/indexes/{index_id}/promote")
    def promote_index(index_id: str, _: Principal = Depends(admin_dep)):
        if hasattr(promotion_controller, 'transition'):
            return promotion_controller.transition(index_id, 'promote', {}).model_dump(mode='json')
        deployment = control_state.indexes.get(index_id)
        updated = promotion_controller.promote(deployment)
        return control_state.indexes.put(updated).model_dump(mode="json")

    @app.post("/v1/control/indexes/{index_id}/rollback-canary")
    def rollback_index(index_id: str, _: Principal = Depends(admin_dep)):
        if hasattr(promotion_controller, 'transition'):
            return promotion_controller.transition(index_id, 'rollback-canary', {}).model_dump(mode='json')
        deployment = control_state.indexes.get(index_id)
        updated = promotion_controller.rollback_canary(deployment)
        return control_state.indexes.put(updated).model_dump(mode="json")

    if service.exporter and hasattr(service.exporter, 'get_for'):
        @app.get('/v1/exports/{job_id}')
        def export_status(job_id: str, principal: Principal = Depends(principal_dep)):
            return service.exporter.get_for(principal, job_id)

        @app.delete('/v1/exports/{job_id}')
        def cancel_export(job_id: str, principal: Principal = Depends(principal_dep)):
            return service.exporter.cancel_for(principal, job_id)

        @app.get('/v1/exports/{job_id}/download')
        def download_export(job_id: str, principal: Principal = Depends(principal_dep)):
            return service.exporter.download(principal, job_id, service)

    app.state.admin_dependency = admin_dep
    app.state.principal_dependency = principal_dep
    return app


def _register_registry_routes(app: FastAPI, name: str, registry, model_cls, admin_dep):
    list_path = f"/v1/control/{name}"
    item_path = f"/v1/control/{name}/{{item_id}}"

    def list_items(_: Principal = Depends(admin_dep)):
        return {name: [x.model_dump(mode="json") for x in registry.list()]}

    list_items.__name__ = f"list_{name}"
    app.get(list_path)(list_items)

    def put_item(item_id: str, body: dict, _: Principal = Depends(admin_dep)):
        item = model_cls.model_validate({**body, "id": item_id})
        return registry.put(item).model_dump(mode="json")

    put_item.__name__ = f"put_{name}"
    app.put(item_path)(put_item)

    def delete_item(item_id: str, expected_revision: int | None = None, _: Principal = Depends(admin_dep)):
        if hasattr(registry, 'store'):
            registry.delete(item_id, expected_revision=expected_revision)
        else:
            registry.delete(item_id)

    delete_item.__name__ = f"delete_{name}"
    app.delete(item_path, status_code=status.HTTP_204_NO_CONTENT)(delete_item)


def _json_error(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    from .context import trace_id
    return JSONResponse(status_code=status_code, content={'detail': detail, 'code': str(status_code), 'trace_id': trace_id()})
