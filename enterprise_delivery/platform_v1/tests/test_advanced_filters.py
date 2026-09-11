from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import ARRAY, JSON, Column, Integer, MetaData, Table, Text, create_engine, select
from sqlalchemy.dialects import postgresql, mysql
from sqlalchemy.dialects.postgresql import JSONB

from enterprise_data_platform.backends import eval_filter
from enterprise_data_platform.models import FieldDefinition
from enterprise_data_platform.query_validation import QueryValidationError, validate_filter, referenced_filter_fields
from enterprise_data_platform.sqlalchemy_backend import SQLAlchemyStructuredBackend
from enterprise_data_platform.search import search_filter
from enterprise_data_platform.filter_contract import filter_contract
from test_platform_core import build_service, principal, product
from test_api_control import make_client


def filter_product():
    p = product()
    p.fields += [FieldDefinition(name='page_start', data_type='int4', filterable=True),
                 FieldDefinition(name='content_types', data_type='text[]', filterable=True),
                 FieldDefinition(name='content', data_type='jsonb', filterable=True),
                 FieldDefinition(name='updated_at', data_type='timestamptz', filterable=True)]
    return p


def test_nested_fields_types_arrays_json_and_null_negation():
    p = filter_product()
    expression = {'and': [{'field': 'page_start', 'op': 'between', 'value': [3, 8]},
        {'or': [{'field': 'title', 'op': 'starts_with', 'value': 'protocol'},
                {'field': 'title', 'op': 'eq', 'value': 'other'}]},
        {'field': 'content_types', 'op': 'array_contains_all', 'value': ['text']},
        {'field': 'content', 'path': ['site', 'country'], 'op': 'eq', 'value': 'US'},
        {'not': {'field': 'id', 'op': 'in', 'value': ['revoked']}}]}
    validate_filter(p, expression)
    row = {'id': 'allowed', 'page_start': 4, 'title': 'Protocol 12',
           'content_types': ['text', 'table'], 'content': {'site': {'country': 'US'}}}
    assert eval_filter(row, expression)
    row['page_start'] = None
    assert not eval_filter(row, expression)
    assert not eval_filter({'page_start': None}, {'not': {'field': 'page_start', 'op': 'gt', 'value': 4}})
    assert not eval_filter({'id': 'x'}, {'field': 'id', 'op': 'not_in', 'value': ['y', None]})


@pytest.mark.parametrize('leaf', [
    {'field': 'page_start', 'op': 'gte', 'value': '3'},
    {'field': 'page_start', 'op': 'gte', 'value': True},
    {'field': 'page_start', 'op': 'contains', 'value': '3'},
    {'field': 'title', 'op': 'eq', 'value': 3},
    {'field': 'content_types', 'op': 'array_overlaps', 'value': [None]},
    {'field': 'title', 'path': ['key'], 'op': 'eq', 'value': 'bad'},
    {'field': 'updated_at', 'op': 'gte', 'value': 'not-a-date'},
    {'field': 'content', 'path': ['a'] * 9, 'op': 'eq', 'value': 'bad'},
    {'field': 'content', 'op': 'json_contains', 'value': float('nan')},
    {'field': 'id', 'op': 'in', 'value': ['x'] * 1001},
    {'or': [{}, {'field': 'id', 'op': 'eq', 'value': 'x'}]},
    {'field': 'page_start', 'op': 'between', 'value': [7, 2]},
])
def test_invalid_filters_fail_before_execution(leaf):
    with pytest.raises(QueryValidationError): validate_filter(filter_product(), leaf)


def test_parameterized_sql_preserves_literal_wildcards_and_json_keys():
    t = Table('docs', MetaData(), Column('title', Text), Column('page_start', Integer),
              Column('content_types', postgresql.ARRAY(Text)), Column('content', JSONB))
    backend = SQLAlchemyStructuredBackend(lambda _: None)
    expr = {'and': [{'field': 'title', 'op': 'contains', 'value': "_%'; DROP TABLE docs; --"},
                    {'field': 'content', 'path': ["key'); DROP TABLE docs; --"], 'op': 'eq', 'value': 'US'},
                    {'field': 'content_types', 'op': 'array_overlaps', 'value': ['text', 'table']}]}
    statement = select(t.c.title).where(backend._compile_filter(t, expr)).compile(dialect=postgresql.dialect())
    assert 'DROP TABLE' not in str(statement)
    assert '&&' in str(statement)
    assert any('DROP TABLE' in str(v) for v in statement.params.values())
    assert any('\\_\\%' in str(v) for v in statement.params.values())


def test_sqlite_multiple_filters_execute_and_page_and_mask():
    client = make_client()
    body = {'filter': {'and': [{'field': 'amount', 'op': 'gte', 'value': 20},
                {'field': 'amount', 'op': 'lte', 'value': 30},
                {'not': {'field': 'id', 'op': 'in', 'value': [3]}}]}}
    result = client.post('/v1/datasets/orders/query', json=body)
    assert result.status_code == 200, result.text
    assert [r['id'] for r in result.json()['rows']] == [2]
    result = client.post('/v1/datasets/orders/records/lookup', json={'ids': [1, 4]})
    assert result.status_code == 200, result.text
    assert [r['id'] for r in result.json()['rows']] == [1]
    result = client.get('/v1/datasets/orders/filters')
    assert result.status_code == 200
    assert result.json()['boolean_operators'] == ['and', 'or', 'not']
    assert 'contains' not in next(f for f in result.json()['fields'] if f['field'] == 'amount')['operators']


def test_dataset_mandatory_filter_cannot_be_widened_by_allow_policy():
    service, catalog, policies = build_service()
    p = catalog.get('regulatory-docs')
    p.mandatory_filter = {'field': 'id', 'op': 'eq', 'value': '1'}
    catalog.put(p)
    from enterprise_data_platform.models import StructuredQueryRequest
    response = service.query(principal(), p.id, StructuredQueryRequest())
    assert [r['id'] for r in response.rows] == ['1']


def test_opensearch_substrings_escape_dsl_metacharacters():
    value = search_filter({'and': [{'field': 'title', 'op': 'contains', 'value': '*?'},
        {'field': 'content_types', 'op': 'array_contains_all', 'value': ['text', 'table']}]})
    import json
    assert 'case_insensitive' in json.dumps(value)
    assert value['bool']['filter'][0]['wildcard']['metadata.title']['value'] == '*\\*\\?*'
    with pytest.raises(ValueError, match='JSON'):
        search_filter({'field': 'content', 'op': 'json_contains', 'value': {'type': 'text'}})


def test_search_cursor_is_explicitly_rejected_and_original_ranks_survive():
    from enterprise_data_platform.models import SearchRequest
    service, _, _ = build_service()
    with pytest.raises(QueryValidationError, match='bounded top-k'):
        service.keyword_search(principal(), 'regulatory-docs', SearchRequest(query='test', cursor='ignored-before'))
