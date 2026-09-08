"""Restricted SQL gateway ingress. No database wire protocol or raw SQL execution."""
import sqlglot
from sqlglot import exp
from .compatibility import resolve_alias
from .context import ExecutionContext, execution_context
from .models import SortField, StructuredQueryRequest


class UnsupportedSQL(ValueError):
    pass


def literal(node):
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal) and not node.this.is_string:
        return -literal(node.this)
    if not isinstance(node, exp.Literal):
        raise UnsupportedSQL('only literal predicate values are supported')
    if node.is_string:
        return node.this
    return float(node.this) if any(x in node.this.lower() for x in ['.', 'e']) else int(node.this)


def column(node):
    if not isinstance(node, exp.Column) or node.table or node.db or isinstance(node.this, exp.Star):
        raise UnsupportedSQL('only unqualified logical columns are supported')
    return node.name


def predicate(node):
    if isinstance(node, exp.Paren):
        return predicate(node.this)
    if isinstance(node, (exp.And, exp.Or)):
        return {'and' if isinstance(node, exp.And) else 'or': [predicate(node.this), predicate(node.expression)]}
    if isinstance(node, exp.Not):
        return {'not': predicate(node.this)}
    mapping = {exp.EQ:'eq', exp.NEQ:'neq', exp.GT:'gt', exp.GTE:'gte', exp.LT:'lt', exp.LTE:'lte'}
    if type(node) in mapping:
        return {'field': column(node.this), 'op': mapping[type(node)], 'value': literal(node.expression)}
    if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
        return {'field': column(node.this), 'op': 'eq', 'value': None}
    if isinstance(node, exp.In) and not node.args.get('query'):
        return {'field': column(node.this), 'op': 'in', 'value': [literal(x) for x in node.expressions]}
    if isinstance(node, exp.Between):
        return {'field': column(node.this), 'op': 'between', 'value': [literal(node.args['low']), literal(node.args['high'])]}
    raise UnsupportedSQL('SQL predicate is outside the governed subset')


def translate(sql):
    if len(sql) > 20000:
        raise UnsupportedSQL('SQL exceeds complexity budget')
    try:
        statements = sqlglot.parse(sql, read='postgres')
    except sqlglot.errors.ParseError as exc:
        raise UnsupportedSQL('invalid SQL') from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise UnsupportedSQL('one SELECT statement is required')
    tree = statements[0]
    if sum(1 for _ in tree.walk()) > 256:
        raise UnsupportedSQL('SQL exceeds complexity budget')
    supported = {'expressions','from_','where','order','limit'}
    if any(value for key,value in tree.args.items() if key not in supported):
        raise UnsupportedSQL('joins, CTEs, grouping, offsets and modifiers are not supported')
    source = tree.args.get('from_')
    table = source.this if source else None
    if not isinstance(table, exp.Table) or table.db or table.catalog or table.alias or not isinstance(table.this, exp.Identifier):
        raise UnsupportedSQL('one unqualified logical dataset alias is required')
    expressions = tree.expressions
    fields = [] if len(expressions) == 1 and isinstance(expressions[0], exp.Star) else [column(x) for x in expressions]
    order = []
    if tree.args.get('order'):
        for term in tree.args['order'].expressions:
            # Service normalizes ordering to NULLS LAST. Explicit NULLS FIRST is
            # rejected, avoiding silent SQL-semantic changes.
            if term.args.get('nulls_first'):
                raise UnsupportedSQL('SQL ingress requires NULLS LAST ordering')
            order.append(SortField(field=column(term.this), direction='desc' if term.args.get('desc') else 'asc'))
    limit = literal(tree.args['limit'].expression) if tree.args.get('limit') else 100
    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise UnsupportedSQL('LIMIT must be 1..10000')
    where = predicate(tree.args['where'].this) if tree.args.get('where') else None
    return table.name, StructuredQueryRequest(select=fields, filter=where, order_by=order, limit=limit)


class SQLGateway:
    def __init__(self, service, aliases):
        self.service, self.aliases = service, aliases

    def execute(self, principal, api_id, sql, cursor=None):
        alias, request = translate(sql)
        binding = resolve_alias(self.aliases, api_id, alias)
        request.cursor = cursor
        with execution_context() as context:
            old = context.workload; context.workload = 'sql'
            try:
                return self.service.query(principal, binding.dataset_id, request)
            finally:
                context.workload = old
