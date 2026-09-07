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


def referenced_filter_fields(expr: Dict[str, Any] | None) -> Set[str]:
    if not expr:
        return set()
    if "and" in expr or "or" in expr:
        key = "and" if "and" in expr else "or"
        values = expr[key]
        if not isinstance(values, list) or not values:
            raise QueryValidationError(f"'{key}' requires a non-empty list")
        fields: Set[str] = set()
        for child in values:
            if not isinstance(child, dict):
                raise QueryValidationError(f"'{key}' children must be objects")
            fields |= referenced_filter_fields(child)
        return fields
    if "not" in expr:
        if not isinstance(expr["not"], dict):
            raise QueryValidationError("'not' requires an object")
        return referenced_filter_fields(expr["not"])

    field = expr.get("field")
    op = expr.get("op")
    if not field or op not in LEAF_OPS:
        raise QueryValidationError("filter leaf requires field and supported op")
    if op != "exists" and "value" not in expr:
        raise QueryValidationError(f"filter op '{op}' requires value")
    if op == "in" and not isinstance(expr.get("value"), list):
        raise QueryValidationError("'in' requires a list value")
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
        requested = [f.name for f in product.fields if f.selectable]
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
