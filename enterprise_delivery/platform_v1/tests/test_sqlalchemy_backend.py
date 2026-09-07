from sqlalchemy import Column, Float, Integer, MetaData, String, Table, create_engine, event

from enterprise_data_platform.catalog import InMemoryCatalog
from enterprise_data_platform.cursor import CursorCodec
from enterprise_data_platform.models import (
    AccessPolicy,
    Capability,
    DataProduct,
    FieldDefinition,
    PolicyEffect,
    Principal,
    ProductStatus,
    SortField,
    SourceBinding,
    StructuredQueryRequest,
)
from enterprise_data_platform.policy import PolicyEngine
from enterprise_data_platform.services import PlatformService
from enterprise_data_platform.sqlalchemy_backend import SQLAlchemyStructuredBackend


def test_sql_backend_uses_pushdown_keyset_and_never_offset():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    md = MetaData()
    orders = Table(
        "orders",
        md,
        Column("id", Integer, primary_key=True, nullable=False),
        Column("tenant_id", String, nullable=False),
        Column("amount", Float, nullable=False),
    )
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(orders.insert(), [
            {"id": i, "tenant_id": "t1" if i <= 10 else "t2", "amount": float((i % 3) * 10)}
            for i in range(1, 21)
        ])

    statements = []
    executions = []
    @event.listens_for(engine, "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        executions.append((statement, parameters))

    product = DataProduct(
        id="orders",
        display_name="Orders",
        version="1",
        status=ProductStatus.ACTIVE,
        source=SourceBinding(connector="sqlite", environment="test", object_name="orders"),
        identity_fields=["id"],
        tenant_field="tenant_id",
        fields=[
            FieldDefinition(name="id", data_type="int", nullable=False, filterable=True, sortable=True),
            FieldDefinition(name="tenant_id", data_type="string", nullable=False, filterable=True, sortable=True),
            FieldDefinition(name="amount", data_type="float", nullable=False, filterable=True, sortable=True),
        ],
        capabilities={Capability.QUERY},
        default_limit=3,
        max_limit=3,
    )
    product.validate_contract()
    catalog = InMemoryCatalog(); catalog.put(product)
    policies = PolicyEngine([AccessPolicy(
        id="allow",
        effect=PolicyEffect.ALLOW,
        dataset_patterns=["orders"],
        operations={Capability.QUERY},
        client_ids={"c"},
        allowed_fields={"id", "tenant_id", "amount"},
        require_tenant_isolation=True,
        max_limit=3,
    )])
    service = PlatformService(
        catalog=catalog,
        policies=policies,
        cursor_codec=CursorCodec(b"q" * 32),
        structured=SQLAlchemyStructuredBackend(lambda p: engine),
    )
    principal = Principal(subject="svc", client_id="c", tenant="t1")
    request = StructuredQueryRequest(select=["id", "amount"], order_by=[SortField(field="amount", direction="asc")], limit=3)

    first = service.query(principal, "orders", request)
    second = service.query(principal, "orders", request.model_copy(update={"cursor": first.next_cursor}))

    ids1 = [r["id"] for r in first.rows]
    ids2 = [r["id"] for r in second.rows]
    assert len(ids1) == 3 and len(ids2) == 3
    assert not set(ids1) & set(ids2)
    assert all(i <= 10 for i in ids1 + ids2)  # mandatory tenant filter was pushed down
    query_sql = "\n".join(s for s in statements if s.lstrip().upper().startswith("SELECT"))
    # SQLite renders LIMIT with an explicit OFFSET parameter even when the
    # application never requested offset pagination. That parameter must stay 0;
    # continuation is expressed by the keyset WHERE predicate instead.
    for statement, params in executions:
        if statement.lstrip().upper().startswith("SELECT") and " OFFSET " in statement.upper() and isinstance(params, tuple):
            assert params[-1] == 0
    assert "tenant_id" in query_sql
    assert "orders.amount >" in query_sql.lower() or "orders.id >" in query_sql.lower()
