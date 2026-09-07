from __future__ import annotations

from fastapi.testclient import TestClient

from enterprise_data_platform.api import create_app
from enterprise_data_platform.backends import InMemoryStructuredBackend
from enterprise_data_platform.catalog import InMemoryCatalog
from enterprise_data_platform.control_state import ControlState
from enterprise_data_platform.cursor import CursorCodec
from enterprise_data_platform.models import (
    AccessPolicy,
    Capability,
    DataProduct,
    FieldDefinition,
    PolicyEffect,
    Principal,
    ProductStatus,
    SourceBinding,
)
from enterprise_data_platform.policy import PolicyEngine
from enterprise_data_platform.operations import InMemoryOperationsProvider
from enterprise_data_platform.services import PlatformService


def make_client() -> TestClient:
    catalog = InMemoryCatalog()
    product = DataProduct(
        id="orders",
        display_name="Orders",
        version="1",
        status=ProductStatus.ACTIVE,
        source=SourceBinding(connector="warehouse", environment="prod", object_name="ORDERS"),
        identity_fields=["id"],
        tenant_field="tenant_id",
        fields=[
            FieldDefinition(name="id", data_type="int", filterable=True, sortable=True),
            FieldDefinition(name="tenant_id", data_type="string", filterable=True, sortable=True),
            FieldDefinition(name="amount", data_type="float", filterable=True, sortable=True),
        ],
        capabilities={Capability.QUERY},
        default_limit=2,
        max_limit=2,
    )
    product.validate_contract()
    catalog.put(product)
    policies = PolicyEngine(
        [
            AccessPolicy(
                id="orders-consumer",
                effect=PolicyEffect.ALLOW,
                dataset_patterns=["orders"],
                operations={Capability.QUERY},
                client_ids={"consumer"},
                allowed_fields={"id", "tenant_id", "amount"},
                require_tenant_isolation=True,
                max_limit=2,
            )
        ]
    )
    service = PlatformService(
        catalog=catalog,
        policies=policies,
        cursor_codec=CursorCodec(b"z" * 32),
        structured=InMemoryStructuredBackend(
            {
                "orders": [
                    {"id": 1, "tenant_id": "t1", "amount": 10.0},
                    {"id": 2, "tenant_id": "t1", "amount": 20.0},
                    {"id": 3, "tenant_id": "t1", "amount": 30.0},
                    {"id": 4, "tenant_id": "t2", "amount": 40.0},
                ]
            }
        ),
    )
    control = ControlState()

    def resolver(request):
        groups = set(filter(None, (request.headers.get("x-groups") or "").split(",")))
        return Principal(
            subject=request.headers.get("x-subject") or "user",
            client_id=request.headers.get("x-client-id") or "consumer",
            tenant=request.headers.get("x-tenant") or "t1",
            groups=groups,
        )

    app = create_app(
        service=service,
        catalog=catalog,
        policies=policies,
        control_state=control,
        principal_resolver=resolver,
        control_admin_check=lambda p: "data-platform-admin" in p.groups,
        operations_provider=InMemoryOperationsProvider({"system_status": "operational", "metrics": {"p95_latency": {"value": 412}}}),
    )
    return TestClient(app)


def test_data_plane_has_no_offset_and_returns_cursor():
    client = make_client()
    first = client.post(
        "/v1/datasets/orders/query",
        json={"select": ["id", "tenant_id", "amount"], "limit": 100},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert [r["id"] for r in body["rows"]] == [1, 2]
    assert body["next_cursor"]

    invalid = client.post(
        "/v1/datasets/orders/query",
        json={"select": ["id"], "limit": 2, "offset": 1000000},
    )
    assert invalid.status_code == 422


def test_control_hub_requires_admin_and_manages_runtime_objects():
    client = make_client()
    assert client.get("/v1/control/overview").status_code == 403
    headers = {"x-groups": "data-platform-admin"}

    put_client = client.put(
        "/v1/control/clients/regassist-prod",
        headers=headers,
        json={
            "display_name": "RegAssist Production",
            "owner": "regulatory-ai",
            "auth_mode": "workload_identity",
            "allowed_datasets": ["orders"],
            "allowed_capabilities": ["query"],
            "rate_limit_rps": 100,
            "max_concurrency": 50,
        },
    )
    assert put_client.status_code == 200, put_client.text

    assert client.put(
        "/v1/control/agents/regassist-agent",
        headers=headers,
        json={
            "display_name": "RegAssist Agent",
            "owner": "regulatory-ai",
            "service_principal": "svc-regassist",
            "allowed_datasets": ["orders"],
            "allowed_capabilities": ["retrieve"],
            "mcp_enabled": True,
        },
    ).status_code == 200

    assert client.put(
        "/v1/control/guardrails/citations-required",
        headers=headers,
        json={
            "name": "Citations required",
            "kind": "require_citations",
            "action": "deny",
            "scope": "agent",
            "target": "regassist-agent",
            "config": {"minimum_sources": 1},
        },
    ).status_code == 200

    assert client.put(
        "/v1/control/indexes/orders-vector",
        headers=headers,
        json={
            "dataset_id": "orders",
            "index_type": "vector",
            "active_version": "v1",
            "state": "healthy",
            "indexed_records": 1_000_000_000,
            "shard_count": 64,
            "replica_count": 2,
        },
    ).status_code == 200

    overview = client.get("/v1/control/overview", headers=headers)
    assert overview.status_code == 200
    data = overview.json()
    assert data["clients"] == 1
    assert data["agents"] == 1
    assert data["guardrails"] == 1
    assert data["indexes"] == 1
    assert data["healthy_indexes"] == 1
    assert data["control_plane_status"] == "healthy"

    dashboard = client.get("/v1/control/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["metrics"]["p95_latency"]["value"] == 412
    assert dashboard.json()["generated_at"].endswith("+00:00")


def test_control_hub_zero_downtime_index_promotion_is_gated():
    client = make_client()
    headers = {"x-groups": "data-platform-admin"}
    put = client.put(
        "/v1/control/indexes/orders-vector",
        headers=headers,
        json={
            "dataset_id": "orders",
            "index_type": "vector",
            "active_version": "v1",
            "candidate_version": "v2",
            "state": "building",
            "freshness_lag_seconds": 10,
            "indexed_records": 1_500_000_000,
            "shard_count": 64,
            "replica_count": 2,
        },
    )
    assert put.status_code == 200

    bad = client.post(
        "/v1/control/indexes/orders-vector/validate",
        headers=headers,
        json={
            "evaluated_queries": 500,
            "recall_at_k": 0.5,
            "precision_at_k": 0.9,
            "citation_coverage": 1.0,
            "p95_latency_ms": 500,
            "error_rate": 0.0001,
        },
    )
    assert bad.status_code == 409

    good = client.post(
        "/v1/control/indexes/orders-vector/validate",
        headers=headers,
        json={
            "evaluated_queries": 500,
            "recall_at_k": 0.95,
            "precision_at_k": 0.9,
            "citation_coverage": 1.0,
            "p95_latency_ms": 500,
            "error_rate": 0.0001,
        },
    )
    assert good.status_code == 200, good.text
    assert good.json()["active_version"] == "v1"

    assert client.post("/v1/control/indexes/orders-vector/canary", headers=headers, json={"percent": 100}).status_code == 200
    promoted = client.post("/v1/control/indexes/orders-vector/promote", headers=headers)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["active_version"] == "v2"
    assert promoted.json()["candidate_version"] is None
