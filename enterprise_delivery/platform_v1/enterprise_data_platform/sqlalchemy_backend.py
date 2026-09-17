from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy import MetaData, Table, and_, case, func, or_, select, text, tuple_
from .cache import BoundedCache, CacheClass, CacheKey
from .context import current_context
from .durable import fingerprint
from sqlalchemy.engine import Engine

from .backends import StructuredBackend, StructuredPage
from .models import CountMode, DataProduct, SortField

EngineProvider = Callable[[DataProduct], Engine]


class SQLAlchemyStructuredBackend(StructuredBackend):
    """Parameterized SQL pushdown backend with keyset pagination.

    The host supplies pooled SQLAlchemy engines. No OFFSET is generated. Query
    filters/projections/order are compiled to SQL and pushed to the source. Table
    metadata is cached by data-product version so schema discovery is not paid on
    every request.
    """

    def __init__(self, engine_provider: EngineProvider, *, timeout_seconds=30, native_nulls_last=True,
                 max_scan_rows=None, allow_unknown_cost=False) -> None:
        self._engine_provider = engine_provider
        self._tables = BoundedCache(max_entries=256, ttl_seconds=300)
        self.timeout_seconds = timeout_seconds
        self.native_nulls_last = native_nulls_last
        self.max_scan_rows, self.allow_unknown_cost = max_scan_rows, allow_unknown_cost
        self._lock = threading.RLock()

    def invalidate(self, dataset_id: str) -> None:
        with self._lock:
            self._tables.invalidate(lambda key: key.key.startswith(dataset_id + ":"))

    def query(
        self,
        *,
        product: DataProduct,
        fields: Sequence[str],
        filter_expr: Optional[Dict[str, Any]],
        order_by: Sequence[SortField],
        limit: int,
        position: Optional[Mapping[str, Any]],
        count_mode: CountMode,
    ) -> StructuredPage:
        engine = self._engine_provider(product)
        table = self._table(product, engine)
        if not 1 <= limit <= 10000:
            raise ValueError("query limit must be 1..10000")
        internal_fields = list(dict.fromkeys([*fields, *[s.field for s in order_by]]))
        columns = [table.c[name] for name in internal_fields]
        stmt = select(*columns).select_from(table)

        if filter_expr:
            stmt = stmt.where(self._compile_filter(table, filter_expr))
        if position:
            stmt = stmt.where(self._keyset_after(table, order_by, position))
        ordering = []
        for item in order_by:
            column = table.c[item.field]
            if self.native_nulls_last:
                ordering.append(self._order_clause(column, item.direction))
            else:
                ordering.extend([case((column.is_(None), 1), else_=0).asc(), column.asc() if item.direction == "asc" else column.desc()])
        stmt = stmt.order_by(*ordering).limit(limit + 1)

        context = current_context.get()
        seconds = min(self.timeout_seconds, context.remaining() if context else self.timeout_seconds)
        with engine.connect() as conn, conn.begin():
            if engine.dialect.name == 'postgresql':
                conn.execute(text('SET TRANSACTION READ ONLY'))
                conn.execute(text("SELECT set_config('statement_timeout', :value, true)"), {'value': str(max(1, int(seconds * 1000)))})
            elif engine.dialect.name == 'mysql':
                conn.execute(text('SET SESSION MAX_EXECUTION_TIME=:value'), {'value': max(1, int(seconds * 1000))})
            elif engine.dialect.name == 'mariadb':
                conn.execute(text('SET SESSION max_statement_time=:value'), {'value': seconds})
            if context:
                context.remaining()
            count_stmt = select(func.count()).select_from(table)
            estimate_stmt = select(*columns).select_from(table)
            if filter_expr:
                predicate = self._compile_filter(table, filter_expr)
                count_stmt = count_stmt.where(predicate)
                estimate_stmt = estimate_stmt.where(predicate)
            # Cost inspection runs only after the caller's policy filters have
            # been combined. EXPLAIN never executes the underlying query.
            self._check_scan(conn, stmt)
            if count_mode == CountMode.EXACT:
                self._check_scan(conn, count_stmt)
            result = conn.execution_options(yield_per=min(1000, limit + 1)).execute(stmt)
            try:
                rows = [dict(r._mapping) for r in result.fetchmany(limit + 1)]
            finally:
                result.close()
            if context:
                context.remaining()
            count = None
            estimate = False
            if count_mode == CountMode.EXACT:
                count = int(conn.execute(count_stmt).scalar_one())
            elif count_mode == CountMode.ESTIMATE:
                stats = self.inspect_plan(conn, estimate_stmt)
                count = stats['estimated_rows'] if stats else None
                estimate = True

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_position = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_position = {item.field: last[item.field] for item in order_by}
        return StructuredPage(rows=page_rows, next_position=next_position, count=count, count_is_estimate=estimate)

    @staticmethod
    def inspect_plan(conn, statement):
        """Sanitized PostgreSQL estimates; never expose physical plan text."""
        if conn.dialect.name != 'postgresql':
            return None
        compiled = statement.compile(dialect=conn.dialect, compile_kwargs={'render_postcompile': True})
        # The statement is built exclusively with SQLAlchemy expressions. User
        # literals stay bound parameters even in the EXPLAIN prefix.
        parameters = {key: compiled._bind_processors[key](value) if key in compiled._bind_processors else value
                      for key, value in compiled.params.items()}
        value = conn.exec_driver_sql('EXPLAIN (FORMAT JSON, VERBOSE) ' + str(compiled), parameters,
            execution_options={'stream_results': False, 'yield_per': None}).scalar_one()
        root = value[0]['Plan']
        scans = []
        def visit(node):
            if 'Scan' in node.get('Node Type', ''):
                # Rows removed by filters are not in Plan Rows. Sequential
                # scans therefore use table cardinality from pg_class below.
                estimate = int(node.get('Plan Rows', 0))
                if (node.get('Node Type') in {'Seq Scan', 'Parallel Seq Scan'} or
                        (node.get('Filter') and not node.get('Index Cond'))) and node.get('Relation Name'):
                    rows = conn.execute(text('SELECT c.reltuples FROM pg_catalog.pg_class c '
                        'JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace '
                        'WHERE c.relname=:name AND n.nspname=COALESCE(:schema,current_schema())'),
                        {'name': node['Relation Name'], 'schema': node.get('Schema')}).scalar()
                    estimate = max(estimate, int(rows or 0))
                scans.append(estimate)
            for child in node.get('Plans', []):
                visit(child)
        visit(root)
        return {'estimated_rows': max(0, int(root.get('Plan Rows', 0))),
            'estimated_scan_rows': sum(scans), 'estimated_cost': float(root.get('Total Cost', 0))}

    def _check_scan(self, conn, statement):
        if self.max_scan_rows is None:
            return
        stats = self.inspect_plan(conn, statement)
        if stats is None:
            if not self.allow_unknown_cost:
                raise ValueError('source has no cost inspector; administrator must configure a native scan governor')
        elif stats['estimated_scan_rows'] > self.max_scan_rows:
            raise ValueError('estimated scan exceeds source budget; narrow the query or use an approved export source')

    def aggregate(self, *, product, request, filter_expr, limit):
        engine = self._engine_provider(product)
        table = self._table(product, engine)
        groups = [table.c[name] for name in request.group_by]
        functions = {'count': func.count, 'sum': func.sum, 'avg': func.avg, 'min': func.min, 'max': func.max}
        measures = [(functions[m.function](table.c[m.field]) if m.field else func.count()).label(m.name)
            for m in request.measures]
        stmt = select(*groups, *measures).select_from(table)
        if filter_expr:
            stmt = stmt.where(self._compile_filter(table, filter_expr))
        if groups:
            stmt = stmt.group_by(*groups).order_by(*groups)
        stmt = stmt.limit(limit + 1)
        context = current_context.get()
        seconds = min(self.timeout_seconds, context.remaining() if context else self.timeout_seconds)
        with engine.connect() as conn, conn.begin():
            if conn.dialect.name == 'postgresql':
                conn.execute(text('SET TRANSACTION READ ONLY'))
                conn.execute(text("SELECT set_config('statement_timeout', :value, true)"), {'value': str(max(1, int(seconds * 1000)))})
            elif conn.dialect.name == 'mysql':
                conn.execute(text('SET SESSION MAX_EXECUTION_TIME=:value'), {'value': max(1, int(seconds * 1000))})
            elif conn.dialect.name == 'mariadb':
                conn.execute(text('SET SESSION max_statement_time=:value'), {'value': seconds})
            self._check_scan(conn, stmt)
            result = conn.execution_options(yield_per=min(limit + 1, 1000)).execute(stmt)
            try:
                rows = [dict(r._mapping) for r in result.fetchmany(limit + 1)]
            finally:
                result.close()
        if context:
            context.remaining()
        return rows

    def _table(self, product: DataProduct, engine: Engine) -> Table:
        key = CacheKey(CacheClass.SCHEMA, 'control', 'schema', product.version,
            product.id + ':' + fingerprint(product.source.model_dump()) + ':' + str(id(engine)))
        existing = self._tables.get(key)
        if existing is not None:
            return existing
        with self._lock:
            existing = self._tables.get(key)
            if existing is not None:
                return existing
            table = Table(product.source.object_name, MetaData(), schema=product.source.schema_name, autoload_with=engine, resolve_fks=False)
            self._tables.put(key, table, size_bytes=max(1024, len(table.columns) * 512))
            return table

    @staticmethod
    def _order_clause(column, direction: str):
        return (column.asc() if direction == "asc" else column.desc()).nullslast()

    def _compile_filter(self, table: Table, expr: Dict[str, Any]):
        if "and" in expr:
            return and_(*[self._compile_filter(table, child) for child in expr["and"]])
        if "or" in expr:
            return or_(*[self._compile_filter(table, child) for child in expr["or"]])
        if "not" in expr:
            return ~self._compile_filter(table, expr["not"])
        col = table.c[expr["field"]]
        op = expr["op"]
        value = expr.get("value")
        if "path" in expr:
            from sqlalchemy import JSON
            if not isinstance(col.type, JSON):
                raise ValueError("source column does not support JSON paths")
            for part in expr["path"]: col = col[part]
            sample = next((v for v in value if v is not None), None) if isinstance(value, list) else value
            from sqlalchemy import Numeric, cast
            from sqlalchemy.dialects.postgresql import JSONB
            source_type = table.c[expr["field"]].type
            if isinstance(source_type, JSONB):
                # Heterogeneous JSON must not cause invalid boolean/numeric
                # casts. CASE also preserves SQL NULL for missing/wrong types.
                if op == 'exists' or sample is None:
                    col = col.as_string()
                else:
                    expected = 'boolean' if type(sample) is bool else 'number' if type(sample) in {int, float} else 'string'
                    converted = col.as_boolean() if expected == 'boolean' else cast(col.as_string(), Numeric()) if expected == 'number' else col.as_string()
                    col = case((func.jsonb_typeof(col) == expected, converted), else_=None)
            elif type(sample) is bool: col = col.as_boolean()
            elif type(sample) is int: col = col.as_integer()
            elif type(sample) is float: col = col.as_float()
            else: col = col.as_string()
        if op in {"array_contains_all", "array_overlaps", "json_contains"}:
            from sqlalchemy.dialects.postgresql import ARRAY, JSONB
            expected = JSONB if op == "json_contains" else ARRAY
            if not isinstance(col.type, expected):
                raise ValueError("source dialect/type does not support this filter operator")
            return col.overlap(value) if op == "array_overlaps" else col.contains(value)
        if op == "array_is_empty":
            from sqlalchemy.dialects.postgresql import ARRAY
            if not isinstance(col.type, ARRAY): raise ValueError("array emptiness requires PostgreSQL array")
            return func.cardinality(col) == 0 if value is not False else func.cardinality(col) > 0
        if op == "eq":
            return col.is_(None) if value is None else col == value
        if op == "neq":
            return col.is_not(None) if value is None else col != value
        if op == "gt":
            return col > value
        if op == "gte":
            return col >= value
        if op == "lt":
            return col < value
        if op == "lte":
            return col <= value
        if op == "in":
            return col.in_(value)
        if op == "not_in":
            return ~col.in_(value)
        if op == "between":
            return col.between(value[0], value[1])
        if op == "contains":
            return col.ilike(f"%{self._escape_like(str(value))}%", escape="\\")
        if op == "starts_with":
            return col.ilike(f"{self._escape_like(str(value))}%", escape="\\")
        if op == "ends_with":
            return col.ilike(f"%{self._escape_like(str(value))}", escape="\\")
        if op == "exists":
            expected = True if value is None else bool(value)
            return col.is_not(None) if expected else col.is_(None)
        raise ValueError(f"unsupported filter op: {op}")

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _keyset_after(table: Table, order_by: Sequence[SortField], position: Mapping[str, Any]):
        """Lexicographic continuation predicate matching ORDER BY ... NULLS LAST."""
        if order_by and all(not table.c[s.field].nullable and position.get(s.field) is not None for s in order_by):
            if len({s.direction for s in order_by}) == 1:
                left = tuple_(*(table.c[s.field] for s in order_by))
                right = tuple_(*(position[s.field] for s in order_by))
                return left > right if order_by[0].direction == 'asc' else left < right
        branches = []
        prefix = []
        for item in order_by:
            col = table.c[item.field]
            value = position.get(item.field)
            if value is None:
                equal = col.is_(None)
                compare = None  # NULLS LAST => nothing is after NULL at this field
            else:
                equal = col == value
                directional = col > value if item.direction == "asc" else col < value
                compare = or_(directional, col.is_(None)) if col.nullable else directional
            if compare is not None:
                branches.append(and_(*prefix, compare))
            prefix.append(equal)
        if not branches:
            # Cursor points at the final all-NULL ordering tuple.
            return False
        return or_(*branches)

