"""Bounded aggregate pushdown under a dedicated capability and policy gate."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Capability, StructuredQueryResponse
from .query_validation import validate_filter, validate_projection
from .policy import and_filters
from .services import AccessDenied, CapabilityUnavailable
from .context import trace_id


class Measure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,63}$')
    function: Literal['count', 'sum', 'avg', 'min', 'max']
    field: str | None = None

    @model_validator(mode='after')
    def required_field(self):
        if self.function != 'count' and not self.field:
            raise ValueError('aggregate function requires a field')
        return self


class AggregateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    group_by: list[str] = Field(default_factory=list, max_length=16)
    measures: list[Measure] = Field(min_length=1, max_length=16)
    filter: dict | None = None
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode='after')
    def unique_names(self):
        names = self.group_by + [m.name for m in self.measures]
        if len(set(names)) != len(names):
            raise ValueError('group fields and measure names must be unique')
        return self


def aggregate(service, principal, dataset_id, request):
    product, decision = service._decision(principal, dataset_id, Capability.AGGREGATE)
    if not hasattr(service.structured, 'aggregate'):
        raise CapabilityUnavailable('aggregate pushdown is not configured')
    service._validate_user_fields(request, decision)
    fields = list(dict.fromkeys(request.group_by + [m.field for m in request.measures if m.field]))
    if set(fields) - (decision.allowed_fields - decision.masked_fields):
        raise AccessDenied('aggregation references restricted fields')
    if fields:
        validate_projection(product, fields, decision.allowed_fields)
    for measure in request.measures:
        if measure.function in {'sum', 'avg'} and product.field_map()[measure.field].data_type.lower() not in {
            'int', 'integer', 'bigint', 'float', 'double', 'decimal', 'numeric'}:
            raise ValueError('sum and avg require a numeric field')
    if any(m.function == 'count' for m in request.measures):
        _, count_decision = service._decision(principal, dataset_id, Capability.EXACT_COUNT)
        decision.mandatory_filter = and_filters(decision.mandatory_filter, count_decision.mandatory_filter)
    combined = and_filters(request.filter, decision.mandatory_filter)
    validate_filter(product, combined)
    limit = min(request.limit, decision.max_limit)
    if service.guardrails:
        rule = service.guardrails.evaluate(principal=principal, product=product, operation=Capability.AGGREGATE,
            requested_fields=fields, limit=limit)
        if rule.max_limit is not None:
            limit = min(limit, rule.max_limit)
    rows = service.structured.aggregate(product=product, request=request, filter_expr=combined, limit=limit)
    return StructuredQueryResponse(rows=rows[:limit], has_more=len(rows) > limit,
        returned_rows=min(limit, len(rows)), trace_id=trace_id())
