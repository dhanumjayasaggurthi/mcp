"""Production service boundary shared by REST, SQL, exports and MCP."""
from functools import wraps
import time
from .context import current_actor, execution_context
from .durable import canonical_json
from .models import Capability, StructuredQueryRequest
from .services import AccessDenied, PlatformService


def governed(operation, workload):
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, principal, dataset_id, request):
            with execution_context() as context:
                previous_workload = context.workload
                if previous_workload == 'interactive':
                    context.workload = workload
                context.deadline = min(context.deadline, time.monotonic() + self.governor.limits.max_seconds)
                actor = current_actor.set({'subject': principal.subject, 'tenant': principal.tenant,
                    'client_id': principal.client_id, 'agent_id': principal.agent_id, 'groups': sorted(principal.groups)})
                started = time.monotonic()
                outcome, row_count, byte_count = 'ok', 0, 0
                if self.metrics:
                    self.metrics.active.add(1, {'operation': operation.value})
                try:
                    product, decision = self._decision(principal, dataset_id, operation)
                    client = self.control.clients.get(principal.client_id)
                    source = product.source.source_id or product.source.connector
                    with self.governor.admit(principal, source, context.workload, context, client):
                        connector = self.structured.resolve(product) if operation in {Capability.QUERY, Capability.AGGREGATE} or isinstance(request, StructuredQueryRequest) else None
                        plan = self.planner.plan(product, decision, operation, context, connector, request=request)
                        self.store.audit('execution.plan', dataset_id, {'strategy': plan.strategy.value, 'operation': operation.value,
                            'stages': list(plan.stages), 'cost_inspection': plan.cost_inspection})
                        execution_request = request.model_copy(update=plan.request_updates) if plan.request_updates else request
                        result = fn(self, principal, dataset_id, execution_request)
                        context.remaining()
                        byte_count = len(result.model_dump_json().encode())
                        row_count = getattr(result, 'returned_rows', len(getattr(result, 'results', [])))
                        if byte_count > self.governor.limits.max_result_bytes:
                            raise ValueError('response exceeds configured byte budget')
                        self.store.audit('execution.complete', dataset_id, {'operation': operation.value})
                        return result
                except Exception as exc:
                    from .governor import Overloaded
                    from .resilience import BackendUnavailable
                    outcome = 'denied' if isinstance(exc, AccessDenied) else 'overloaded' if isinstance(exc, Overloaded) else 'timeout' if isinstance(exc, TimeoutError) else 'unavailable' if isinstance(exc, BackendUnavailable) else 'error'
                    self.store.audit('execution.failed', dataset_id, {'operation': operation.value, 'error_type': type(exc).__name__})
                    raise
                finally:
                    if self.metrics:
                        self.metrics.record(operation.value, time.monotonic() - started, outcome, row_count, byte_count)
                        self.metrics.active.add(-1, {'operation': operation.value})
                    current_actor.reset(actor)
                    context.workload = previous_workload
        return wrapped
    return decorate


class GovernedService(PlatformService):
    def __init__(self, *, store, control, governor, planner, metrics=None, **kwargs):
        super().__init__(**kwargs)
        self.store, self.control, self.governor, self.planner, self.metrics = store, control, governor, planner, metrics

    def _decision(self, principal, dataset_id, operation):
        from .control_state import ResourceNotFound
        actor = {'subject': principal.subject, 'client_id': principal.client_id, 'tenant': principal.tenant, 'agent_id': principal.agent_id}
        try:
            if principal.attributes.get('identity_expires') and time.time() >= float(principal.attributes['identity_expires']):
                raise AccessDenied('identity grant expired')
            if 'edp:' + operation.value not in principal.attributes.get('oauth_scope', '').split():
                raise AccessDenied('OAuth scope does not permit this operation')
            if not principal.client_id:
                raise AccessDenied('registered client identity is required')
            client = self.control.clients.get(principal.client_id)
            if client.status.value != 'active' or dataset_id not in client.allowed_datasets or operation not in client.allowed_capabilities:
                raise AccessDenied('client registration does not permit this operation')
            if principal.agent_id:
                agent = self.control.agents.get(principal.agent_id)
                if agent.status.value != 'active' or agent.service_principal != principal.subject or dataset_id not in agent.allowed_datasets or operation not in agent.allowed_capabilities:
                    raise AccessDenied('agent registration does not permit this operation')
            product, decision = super()._decision(principal, dataset_id, operation)
            if principal.agent_id:
                decision.max_top_k = min(decision.max_top_k, agent.max_top_k)
            if product.retrieval and operation in {Capability.KEYWORD, Capability.VECTOR, Capability.HYBRID, Capability.RETRIEVE}:
                try:
                    route = self.store.get('routing', dataset_id)['payload']
                except ResourceNotFound:
                    route = None
                if route:
                    from .context import trace_id
                    from .durable import fingerprint
                    selected = route['active_version']
                    if route.get('candidate_version') and int(fingerprint(trace_id())[:8], 16) % 100 < route.get('traffic_percent', 0):
                        selected = route['candidate_version']
                    if product.retrieval.vector:
                        product.retrieval.vector.index_version = selected
                    else:
                        product.retrieval.keyword_index = selected
        except (AccessDenied, ResourceNotFound) as exc:
            self.store.audit('authorization.deny', dataset_id, {'operation': operation.value}, actor=actor)
            raise AccessDenied('operation is not authorized') from exc
        self.store.audit('authorization.allow', dataset_id, {'operation': operation.value, 'policies': decision.matched_policy_ids}, actor=actor)
        return product, decision

    @governed(Capability.QUERY, 'interactive')
    def query(self, principal, dataset_id, request):
        return super().query(principal, dataset_id, request)

    @governed(Capability.AGGREGATE, 'sql')
    def aggregate(self, principal, dataset_id, request):
        return super().aggregate(principal, dataset_id, request)

    @governed(Capability.EXPORT, 'export')
    def export_page(self, principal, dataset_id, request):
        return super().export_page(principal, dataset_id, request)

    @governed(Capability.KEYWORD, 'rag')
    def keyword_search(self, principal, dataset_id, request):
        return super().keyword_search(principal, dataset_id, request)

    @governed(Capability.VECTOR, 'rag')
    def vector_search(self, principal, dataset_id, request):
        return super().vector_search(principal, dataset_id, request)

    @governed(Capability.HYBRID, 'rag')
    def hybrid_search(self, principal, dataset_id, request):
        return super().hybrid_search(principal, dataset_id, request)

    @governed(Capability.RETRIEVE, 'rag')
    def retrieve(self, principal, dataset_id, request):
        return super().retrieve(principal, dataset_id, request)

    @governed(Capability.EXPORT, 'export')
    def export(self, principal, dataset_id, request):
        return super().export(principal, dataset_id, request)
