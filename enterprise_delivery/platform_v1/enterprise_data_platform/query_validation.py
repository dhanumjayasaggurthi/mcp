from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

from .models import DataProduct, SortField


class QueryValidationError(ValueError):
    pass


LEAF_OPS = {
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "between",
    "contains",
    "starts_with",
    "exists",
}


def referenced_filter_fields(expr: Dict[str, Any] | None, _depth=0, _budget=None) -> Set[str]:
    if _budget is None:
        _budget = [0]
    _budget[0] += 1
    if _depth > 12 or _budget[0] > 256:
        raise QueryValidationError("filter complexity limit exceeded")
    if not expr:
        return set()
    if not isinstance(expr, dict):
        raise QueryValidationError("filter must be an object")
    if "and" in expr or "or" in expr:
        key = "and" if "and" in expr else "or"
        if set(expr) != {key}:
            raise QueryValidationError("ambiguous filter expression")
        values = expr[key]
        if not isinstance(values, list) or not values:
            raise QueryValidationError(f"'{key}' requires a non-empty list")
        fields: Set[str] = set()
        for child in values:
            if not isinstance(child, dict):
                raise QueryValidationError(f"'{key}' children must be objects")
            fields |= referenced_filter_fields(child, _depth + 1, _budget)
        return fields
    if "not" in expr:
        if set(expr) != {"not"}:
            raise QueryValidationError("ambiguous filter expression")
        if not isinstance(expr["not"], dict):
            raise QueryValidationError("'not' requires an object")
        return referenced_filter_fields(expr["not"], _depth + 1, _budget)

    field = expr.get("field")
    op = expr.get("op")
    if set(expr) - {"field", "op", "value"} or not isinstance(field, str) or not field or op not in LEAF_OPS:
        raise QueryValidationError("filter leaf requires field and supported op")
    if op != "exists" and "value" not in expr:
        raise QueryValidationError(f"filter op '{op}' requires value")
    if op == "in" and not isinstance(expr.get("value"), list):
        raise QueryValidationError("'in' requires a list value")
    value = expr.get("value")
    if op == "in" and (not value or len(value) > 1000):
        raise QueryValidationError("'in' requires 1..1000 values")
    if op in {'gt','gte','lt','lte','contains','starts_with'} and value is None:
        raise QueryValidationError('operator requires a non-null value')
    if op == 'between' and isinstance(value, list) and None in value:
        raise QueryValidationError('range bounds cannot be null')
    values = value if isinstance(value, list) else [value]
    if any(isinstance(v, (dict, list)) for v in values):
        raise QueryValidationError("filter values must be scalar")
    if op == "exists" and value is not None and not isinstance(value, bool):
        raise QueryValidationError("exists requires a boolean")
    if op == "between":
        value = expr.get("value")
        if not isinstance(value, list) or len(value) != 2:
            raise QueryValidationError("'between' requires [lower, upper]")
    return {str(field)}


def validate_filter(product: DataProduct, expr: Dict[str, Any] | None) -> None:
    fmap = product.field_map()
    for name in referenced_filter_fields(expr):
        field = fmap.get(name)
        if field is None:
            raise QueryValidationError(f"unknown filter field '{name}'")
        if not field.filterable:
            raise QueryValidationError(f"field '{name}' is not filterable")


def validate_projection(product: DataProduct, fields: Iterable[str], allowed_fields: Set[str]) -> List[str]:
    fmap = product.field_map()
    requested = list(fields)
    if not requested:
        requested = [f.name for f in product.fields if f.selectable and f.name in allowed_fields]
    unknown = [name for name in requested if name not in fmap]
    if unknown:
        raise QueryValidationError(f"unknown fields: {unknown}")
    denied = [name for name in requested if name not in allowed_fields]
    if denied:
        raise QueryValidationError(f"fields not permitted: {denied}")
    return requested


def stable_order(product: DataProduct, requested: Iterable[SortField]) -> List[SortField]:
    fmap = product.field_map()
    order = list(requested)
    if len(order) > 16 or len({i.field for i in order}) != len(order):
        raise QueryValidationError("sort fields must be unique and bounded")
    for item in order:
        if item.field not in fmap:
            raise QueryValidationError(f"unknown sort field '{item.field}'")
        if not fmap[item.field].sortable:
            raise QueryValidationError(f"field '{item.field}' is not sortable")
    existing = {x.field for x in order}
    # Every page is deterministically ordered by appending the product's unique
    # identity fields as tie breakers. This is mandatory for keyset pagination.
    for field in product.identity_fields:
        if field not in existing:
            if not fmap[field].sortable:
                raise QueryValidationError(
                    f"identity field '{field}' must be sortable for cursor pagination"
                )
            order.append(SortField(field=field, direction="asc"))
    return order
