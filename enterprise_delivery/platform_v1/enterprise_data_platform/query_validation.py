from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

from .models import DataProduct, SortField


class QueryValidationError(ValueError):
    pass


SCALAR_OPS = {"eq", "neq", "in", "not_in", "exists"}
RANGE_OPS = {"gt", "gte", "lt", "lte", "between"}
TEXT_OPS = {"contains", "starts_with", "ends_with"}
ARRAY_OPS = {"array_contains_all", "array_overlaps"}
LEAF_OPS = SCALAR_OPS | RANGE_OPS | TEXT_OPS | ARRAY_OPS | {"json_contains", "array_is_empty"}


def field_family(data_type):
    name = data_type.lower().strip()
    if name.endswith("[]") or name in {"array", "_text"}: return "array"
    if name in {"json", "jsonb"}: return "json"
    if name.startswith(("vector", "halfvec", "sparsevec", "tsvector", "bytea", "binary")): return "opaque"
    if name.startswith(("int", "bigint", "smallint", "serial", "bigserial")): return "integer"
    if name.startswith(("float", "numeric", "decimal", "double", "real")): return "number"
    if name.startswith(("timestamp", "datetime")): return "datetime"
    if name == "date": return "date"
    if name in {"bool", "boolean"}: return "boolean"
    if name == "uuid": return "uuid"
    return "text"


def field_operators(data_type):
    family = field_family(data_type)
    if family == "opaque": return set()
    if family == "array": return {"exists", "array_is_empty"} | ARRAY_OPS
    if family == "json": return {"exists", "json_contains"}
    if family in {"boolean", "uuid"}: return SCALAR_OPS
    return SCALAR_OPS | RANGE_OPS | (TEXT_OPS if family == "text" else set())


def referenced_filter_fields(expr: Dict[str, Any] | None, _depth=0, _budget=None) -> Set[str]:
    import json
    if _budget is None:
        _budget = [0]
        try:
            if len(json.dumps(expr, allow_nan=False).encode()) > 65536:
                raise QueryValidationError("filter byte budget exceeded")
        except (ValueError, TypeError, RecursionError) as exc:
            raise QueryValidationError("filter must contain bounded finite JSON values") from exc
    _budget[0] += 1
    if _depth > 12 or _budget[0] > 256:
        raise QueryValidationError("filter complexity limit exceeded")
    if expr is None or expr == {}:
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
        fields = set()
        for child in values:
            if not isinstance(child, dict) or not child:
                raise QueryValidationError("boolean children must be non-empty objects")
            fields |= referenced_filter_fields(child, _depth + 1, _budget)
        return fields
    if "not" in expr:
        if set(expr) != {"not"} or not isinstance(expr["not"], dict) or not expr["not"]:
            raise QueryValidationError("not requires one non-empty object")
        return referenced_filter_fields(expr["not"], _depth + 1, _budget)
    field, op, value = expr.get("field"), expr.get("op"), expr.get("value")
    if set(expr) - {"field", "op", "value", "path"} or not isinstance(field, str) or not field or op not in LEAF_OPS:
        raise QueryValidationError("filter leaf requires field and supported op")
    path = expr.get("path")
    if path is not None and (not isinstance(path, list) or not 1 <= len(path) <= 8 or
        any(not (isinstance(x, str) and 0 < len(x) <= 128 or type(x) is int and 0 <= x <= 10000) for x in path)):
        raise QueryValidationError("JSON path must contain 1..8 bounded keys or indices")
    if op != "exists" and "value" not in expr:
        raise QueryValidationError(f"filter op '{op}' requires value")
    if op in {"in", "not_in"} | ARRAY_OPS:
        if not isinstance(value, list) or not 1 <= len(value) <= 1000:
            raise QueryValidationError("list operator requires 1..1000 values")
        if op in ARRAY_OPS and any(x is None for x in value):
            raise QueryValidationError("array operands cannot contain null")
    if op == "between" and (not isinstance(value, list) or len(value) != 2 or None in value):
        raise QueryValidationError("between requires two non-null bounds")
    if op == "json_contains":
        if path is not None or not isinstance(value, (dict, list)):
            raise QueryValidationError("json_contains requires an object or array without path")
        def depth(v, n=0):
            if n > 8: raise QueryValidationError("JSON value nesting exceeded")
            if isinstance(v, dict):
                for item in v.values(): depth(item, n + 1)
            if isinstance(v, list):
                for item in v: depth(item, n + 1)
        depth(value)
    elif any(isinstance(v, (dict, list)) for v in (value if isinstance(value, list) else [value])):
        raise QueryValidationError("filter values must be scalar")
    if op in {"exists", "array_is_empty"} and value is not None and type(value) is not bool:
        raise QueryValidationError("exists requires a boolean")
    if op in TEXT_OPS and (not isinstance(value, str) or not value or len(value) > 4096):
        raise QueryValidationError("text operator requires 1..4096 characters")
    if op in RANGE_OPS and value is None:
        raise QueryValidationError("range operators require non-null values")
    return {field}


def validate_filter(product: DataProduct, expr: Dict[str, Any] | None) -> None:
    from datetime import date, datetime
    from uuid import UUID
    fmap = product.field_map()
    for name in referenced_filter_fields(expr):
        field = fmap.get(name)
        if field is None:
            raise QueryValidationError(f"unknown filter field '{name}'")
        if not field.filterable:
            raise QueryValidationError(f"field '{name}' is not filterable")
    def visit(node):
        if not node: return
        for key in ["and", "or"]:
            if key in node:
                for item in node[key]: visit(item)
                return
        if "not" in node: return visit(node["not"])
        field = fmap[node["field"]]
        family, op, value = field_family(field.data_type), node["op"], node.get("value")
        if "path" in node:
            if family != "json": raise QueryValidationError("path is only valid on JSON fields")
            if op not in SCALAR_OPS | RANGE_OPS | TEXT_OPS:
                raise QueryValidationError("unsupported JSON path operation")
            return
        if op not in field_operators(field.data_type):
            raise QueryValidationError(f"operator '{op}' is not valid for {field.data_type}")
        if op in {"exists", "array_is_empty", "json_contains"}: return
        values = value if isinstance(value, list) else [value]
        if family == "array":
            family = field_family(field.data_type.removesuffix("[]")) if field.data_type.endswith("[]") else "text"
        for v in values:
            if v is None: continue
            valid = True
            if family == "integer": valid = type(v) is int
            elif family == "number": valid = type(v) in {int, float}
            elif family == "boolean": valid = type(v) is bool
            elif family == "text": valid = isinstance(v, str)
            elif family in {"date", "datetime", "uuid"}:
                try:
                    if not isinstance(v, str): raise ValueError()
                    {"date": date.fromisoformat, "datetime": datetime.fromisoformat, "uuid": UUID}[family](v)
                except ValueError: valid = False
            if not valid: raise QueryValidationError(f"filter value has wrong type for '{field.name}'")
        if op == "between":
            try:
                if value[0] > value[1]: raise QueryValidationError("range lower bound exceeds upper bound")
            except TypeError as exc: raise QueryValidationError("incompatible range bounds") from exc
    visit(expr)


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

