"""Capability-aware governed planning. Unsupported strategies fail closed."""
from dataclasses import dataclass, field
from enum import Enum
from .models import Capability, StructuredQueryRequest
from .services import CapabilityUnavailable


class Strategy(str, Enum):
    DIRECT = 'direct_pushdown'
    PARTITIONED = 'parallel_partition_pushdown'
    FEDERATED = 'federated'
    CACHED = 'cached_result'
    MATERIALIZED = 'materialized_acceleration'
    KEYWORD = 'keyword_index'
    VECTOR = 'vector_ann'
    HYBRID = 'hybrid'
    HYDRATE = 'canonical_hydration'
    EXPORT = 'async_export'
    PRECOMPUTED = 'precomputed_product'


@dataclass(frozen=True)
class ExecutionPlan:
    engine_name: str
    strategy: Strategy
    dataset_id: str
    dataset_version: str
    workload: str
    policy_ids: tuple[str, ...]
    timeout_seconds: float
    max_rows: int
    reason: str
    stages: tuple[str, ...] = ()
    request_updates: dict = field(default_factory=dict)
    cost_inspection: bool = False


class GovernedPlanner:
    def __init__(self, name='Adaptive Governed Execution Engine'):
        self.name = name

    def plan(self, product, decision, operation, context, connector=None, *, request=None):
        if not decision.allowed:
            raise PermissionError('planner requires an allowed policy decision')
        strategies = {Capability.QUERY: Strategy.DIRECT, Capability.EXACT_COUNT: Strategy.DIRECT,
            Capability.AGGREGATE: Strategy.DIRECT,
            Capability.KEYWORD: Strategy.KEYWORD, Capability.VECTOR: Strategy.VECTOR,
            Capability.HYBRID: Strategy.HYBRID, Capability.RETRIEVE: Strategy.HYDRATE, Capability.EXPORT: Strategy.EXPORT}
        if operation not in strategies:
            raise CapabilityUnavailable('no executable plan for operation')
        structured = operation in {Capability.QUERY, Capability.EXACT_COUNT, Capability.AGGREGATE} or isinstance(request, StructuredQueryRequest)
        if structured:
            if connector is None or not all([connector.capabilities.predicate, connector.capabilities.projection,
                                            connector.capabilities.ordering, connector.capabilities.keyset]):
                raise CapabilityUnavailable('source cannot safely push down this governed query')
            if operation == Capability.AGGREGATE and not connector.capabilities.aggregation:
                raise CapabilityUnavailable('source cannot push down governed aggregates')
        strategy = Strategy.DIRECT if structured else strategies[operation]
        stages, updates = [strategy.value], {}
        reason = 'Capability-compatible pushdown with source cost inspection and bounded result delivery' if structured else 'Use configured retrieval accelerators and verify canonical ACLs'
        if operation == Capability.RETRIEVE:
            mode = request.mode
            if mode == 'hybrid' and context.remaining() < 1 and product.retrieval.allow_keyword_fallback:
                mode, updates = 'keyword', {'mode': 'keyword'}
                reason = 'Explicitly allowed keyword fallback fits the remaining request deadline'
            stages = {'keyword': ['keyword_index'], 'vector': ['embedding', 'vector_ann'],
                'hybrid': ['keyword_index', 'embedding', 'vector_ann', 'rrf_fusion']}[mode]
        elif operation == Capability.HYBRID:
            stages = ['keyword_index', 'embedding', 'vector_ann', 'rrf_fusion']
        if operation in {Capability.KEYWORD, Capability.VECTOR, Capability.HYBRID, Capability.RETRIEVE}:
            stages += ['canonical_hydration', 'final_authorization']
            if product.retrieval.rerank_profile:
                stages += ['rerank']
            stages += ['deduplication', 'diversification']
        return ExecutionPlan(self.name, strategy, product.id, product.version, context.workload,
            tuple(decision.matched_policy_ids), context.remaining(), min(getattr(request, 'limit', decision.max_limit), decision.max_limit),
            reason, tuple(stages), updates, bool(connector and connector.capabilities.plan_inspection))
