"""Logical aliases preserve the simple consumer API over the governed service."""
from pydantic import BaseModel, ConfigDict, Field
from fastapi import Depends, Query, Response
from .models import Capability, Principal, StructuredQueryRequest
from .services import AccessDenied


class AliasRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    revision: int = Field(default=0, ge=0)
    api_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    alias: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
    dataset_id: str
    row_limit: int = Field(default=1000, ge=1, le=10000)
    enabled: bool = True


def resolve_alias(registry, api_id, alias):
    item = registry.get(api_id + '.' + alias)
    if not item.enabled or item.api_id != api_id or item.alias != alias:
        raise AccessDenied('alias is unavailable')
    return item


def register_compatibility(app, service, aliases, principal_dep):
    def deprecation(response):
        response.headers['Deprecation'] = 'true'
        response.headers['Link'] = '</docs>; rel="deprecation"'

    @app.get('/data-api/{api_id}/tables')
    def tables(api_id: str, response: Response, principal: Principal = Depends(principal_dep)):
        deprecation(response)
        values = []
        for item in aliases.list():
            if item.api_id != api_id or not item.enabled:
                continue
            try:
                service._decision(principal, item.dataset_id, Capability.QUERY)
            except AccessDenied:
                continue
            values.append({'alias': item.alias, 'row_limit': item.row_limit})
        return {'api_id': api_id, 'tables': values}

    @app.get('/data-api/{api_id}/{alias}/columns')
    def columns(api_id: str, alias: str, response: Response, principal: Principal = Depends(principal_dep)):
        deprecation(response)
        item = resolve_alias(aliases, api_id, alias)
        product, decision = service._decision(principal, item.dataset_id, Capability.QUERY)
        return {'alias': alias, 'columns': [{'name': f.name, 'type': f.data_type} for f in product.fields if f.name in decision.allowed_fields]}

    @app.get('/data-api/{api_id}/{alias}/rows')
    def rows(api_id: str, alias: str, response: Response, limit: int = Query(100, ge=1, le=10000),
        offset: int = Query(0, ge=0, le=1000), cursor: str | None = None, search: str = '', column: str = '',
        include_total: bool = False, principal: Principal = Depends(principal_dep)):
        deprecation(response)
        item = resolve_alias(aliases, api_id, alias)
        if cursor and offset:
            raise ValueError('use cursor or bounded offset, not both')
        if search and not column:
            raise ValueError('search requires an approved filterable column')
        expr = {'field': column, 'op': 'contains', 'value': search} if search else None
        skipped, hops = 0, 0
        while skipped < offset:
            if hops >= 20:
                raise ValueError('legacy pagination budget exceeded; use next_cursor')
            page = service.query(principal, item.dataset_id, StructuredQueryRequest(filter=expr,
                limit=min(offset-skipped, item.row_limit), cursor=cursor))
            skipped += len(page.rows); hops += 1; cursor = page.next_cursor
            if not cursor:
                return {'alias': alias, 'rows': [], 'row_count': 0, 'returned_rows': 0, 'has_more': False,
                    'next_cursor': None, 'next_offset': None, 'offset': offset, 'limit': limit, 'total_rows': None, 'max_offset': 1000}
        page = service.query(principal, item.dataset_id, StructuredQueryRequest(filter=expr, limit=min(limit,item.row_limit),
            cursor=cursor, count_mode='exact' if include_total else 'none'))
        return {'alias': alias, 'rows': page.rows, 'row_count': len(page.rows), 'returned_rows': len(page.rows),
            'total_rows': page.count, 'max_offset': 1000, 'offset': offset, 'limit': min(limit,item.row_limit),
            'has_more': page.has_more, 'next_cursor': page.next_cursor,
            'next_offset': offset + len(page.rows) if page.has_more and offset + len(page.rows) <= 1000 else None,
            'search': search or None, 'column': column or None}

    @app.get('/data-api/{api_id}/{alias}/count')
    def count(api_id: str, alias: str, response: Response, mode: str = 'exact', principal: Principal = Depends(principal_dep)):
        deprecation(response)
        item = resolve_alias(aliases, api_id, alias)
        page = service.query(principal, item.dataset_id, StructuredQueryRequest(limit=1, count_mode=mode))
        return {'alias': alias, 'total_rows': page.count, 'count_is_estimate': page.count_is_estimate,
            'max_offset': 1000, 'row_limit': item.row_limit}
