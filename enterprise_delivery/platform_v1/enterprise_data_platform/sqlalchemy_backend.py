from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy import MetaData, Table, and_, func, or_, select
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

    def __init__(self, engine_provider: EngineProvider) -> None:
        self._engine_provider = engine_provider
        self._tables: Dict[Tuple[str, str], Table] = {}
        self._lock = threading.RLock()

    def invalidate(self, dataset_id: str) -> None:
        with self._lock:
            for key in [k for k in self._tables if k[0] == dataset_id]:
                self._tables.pop(key, None)

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
        columns = [table.c[name] for name in fields]
        stmt = select(*columns).select_from(table)

        if filter_expr:
            stmt = stmt.where(self._compile_filter(table, filter_expr))
        if position:
            stmt = stmt.where(self._keyset_after(table, order_by, position))
        stmt = stmt.order_by(*[self._order_clause(table.c[item.field], item.direction) for item in order_by]).limit(limit + 1)

        with engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(stmt).fetchall()]
            count = None
            estimate = False
            if count_mode == CountMode.EXACT:
                count_stmt = select(func.count()).select_from(table)
                if filter_expr:
                    count_stmt = count_stmt.where(self._compile_filter(table, filter_expr))
                count = int(conn.execute(count_stmt).scalar_one())
            elif count_mode == CountMode.ESTIMATE:
                # Generic SQL has no portable low-cost estimate. Dialect-specific
                # adapters may override this method to use source statistics.
                estimate = True

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_position = None
        if has_more and page_rows:
            last = page_rows[-1]
            # Ordering fields might not have been selected by the client. Fetch
            # them in the SQL projection internally to build the cursor safely.
            missing_order_fields = [item.field for item in order_by if item.field not in last]
            if missing_order_fields:
                # Re-run only the final row by its unique identity fields. This
                # should be rare; service integrations should include hidden order
                # fields in backend projections for one-pass execution.
                identity_filter = and_(*[table.c[f] == last[f] for f in product.identity_fields if f in last])
                if len(product.identity_fields) != len([f for f in product.identity_fields if f in last]):
                    raise ValueError("identity fields must be projected internally for cursor pagination")
                extra_stmt = select(*[table.c[item.field] for item in order_by]).where(identity_filter).limit(1)
                with engine.connect() as conn:
                    order_row = conn.execute(extra_stmt).mappings().first()
                if order_row is None:
                    raise RuntimeError("could not resolve continuation position")
                next_position = {item.field: order_row[item.field] for item in order_by}
            else:
                next_position = {item.field: last[item.field] for item in order_by}
        return StructuredPage(rows=page_rows, next_position=next_position, count=count, count_is_estimate=estimate)

    def _table(self, product: DataProduct, engine: Engine) -> Table:
        key = (product.id, product.version)
        with self._lock:
            existing = self._tables.get(key)
            if existing is not None:
                return existing
            metadata = MetaData()
            table = Table(
                product.source.object_name,
                metadata,
                schema=product.source.schema_name,
                autoload_with=engine,
            )
            self._tables[key] = table
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
        if op == "between":
            return col.between(value[0], value[1])
        if op == "contains":
            return col.ilike(f"%{self._escape_like(str(value))}%", escape="\\")
        if op == "starts_with":
            return col.ilike(f"{self._escape_like(str(value))}%", escape="\\")
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
                compare = or_(directional, col.is_(None))
            if compare is not None:
                branches.append(and_(*prefix, compare))
            prefix.append(equal)
        if not branches:
            # Cursor points at the final all-NULL ordering tuple.
            return False
        return or_(*branches)
