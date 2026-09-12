from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from enterprise_data_platform.durable import audits
from enterprise_data_platform.observability import Metrics
from test_durable_security import store
from ui_server import build_console_app

HEADERS = {"Authorization": "Bearer ui-test-token"}


def test_console_session_and_admin_reads_fail_closed(store):
    with TestClient(build_console_app(store)) as client:
        assert client.get("/v1/control/session").status_code == 401
        reader = {"Authorization": "Bearer ui-reader-token"}
        identity = client.get("/v1/control/session", headers=reader).json()
        assert identity["subject"] == "user" and not identity["is_admin"]
        assert identity["routes"] == []
        for path in ["audit", "telemetry", "mcp/tools", "clients"]:
            assert client.get("/v1/control/" + path, headers=reader).status_code == 403
        session = client.get("/v1/control/session", headers=HEADERS).json()
        assert session["is_admin"] and session["runtime_mode"] == "test"
        assert "/v1/control/onboarding/preview" in session["routes"]
        assert client.get("/v1/control/mcp/tools", headers=HEADERS).json()["surface"] == "governed_facade"


def test_registry_pages_and_stale_writes(store):
    with TestClient(build_console_app(store)) as client:
        for name in ["b", "c"]:
            assert client.put("/v1/control/clients/" + name, headers=HEADERS,
                json={"display_name": name, "owner": "test", "status": "disabled"}).status_code == 200
        first = client.get("/v1/control/clients?limit=2", headers=HEADERS).json()
        second = client.get("/v1/control/clients", params={"limit": 2, "after": first["next_cursor"]}, headers=HEADERS).json()
        assert [x["id"] for x in first["clients"] + second["clients"]] == ["app", "b", "c"]
        assert second["next_cursor"] is None
        assert client.get("/v1/control/clients?limit=201", headers=HEADERS).status_code == 422
        old = first["clients"][1]
        assert client.put("/v1/control/clients/b", headers=HEADERS, json={**old, "display_name": "changed"}).status_code == 200
        assert client.put("/v1/control/clients/b", headers=HEADERS, json=old).status_code == 409


def test_audit_tied_timestamps_filters_and_private_fields(store):
    app = build_console_app(store)
    timestamp = datetime.now(timezone.utc).isoformat()
    with store.engine.begin() as conn:
        conn.execute(insert(audits), [{"id": f"event-{n}", "created_at": timestamp, "action": "test.event",
            "resource": "test.resource", "actor": {"subject": "operator", "secret": "must-not-appear"},
            "trace_id": "trace", "details": {"raw_query": "must-not-appear"}} for n in range(5)])
    with TestClient(app) as client:
        params = {"limit": 2, "action": "test.event", "subject": "operator", "resource": "test.resource"}
        events = []
        while True:
            response = client.get("/v1/control/audit", params=params, headers=HEADERS)
            assert response.status_code == 200, response.text
            assert "must-not-appear" not in response.text
            data = response.json()
            events.extend(data["events"])
            if not data["next_cursor"]:
                break
            params.update(data["next_cursor"])
            params["since"] = data["since"]
        assert [x["id"] for x in events] == [f"event-{n}" for n in reversed(range(5))]
        assert client.get("/v1/control/audit?subject=absent", headers=HEADERS).json()["events"] == []
        assert client.get("/v1/control/audit?before_id=event-1", headers=HEADERS).status_code == 400
        assert client.get("/v1/control/audit?limit=101", headers=HEADERS).status_code == 422


def test_observed_telemetry_expires_and_separates_denials(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("enterprise_data_platform.observability.time.monotonic", lambda: clock[0])
    metrics = Metrics()
    assert metrics.snapshot()["totals"]["p95_ms"] is None
    metrics.record("query", .01, rows=2)
    metrics.record("query", .03, outcome="denied")
    metrics.record("retrieve", .07, outcome="error")
    totals = metrics.snapshot()["totals"]
    assert totals["requests"] == 3 and totals["returned_rows"] == 2
    assert totals["denied"] == 1 and totals["error_rate"] == 33.33
    assert totals["p95_ms"] == 70
    clock[0] += 301
    assert metrics.snapshot()["totals"]["requests"] == 0
    assert metrics.snapshot()["totals"]["error_rate"] is None


def test_real_source_inspection_and_compound_query_remain_governed(store):
    with TestClient(build_console_app(store)) as client:
        assert client.post("/v1/control/sources/source/check", headers=HEADERS).json()["status"] == "reachable"
        preview = client.post("/v1/control/onboarding/preview", headers=HEADERS,
            json={"source_id": "source", "object_name": "facts", "dataset_id": "draft", "display_name": "Draft", "template": "table"})
        assert preview.status_code == 200, preview.text
        assert {f["name"] for f in preview.json()["dataset"]["fields"]} == {"id", "tenant", "amount"}
        response = client.post("/v1/datasets/facts/query", headers=HEADERS, json={
            "select": ["id", "amount"], "filter": {"and": [
                {"field": "amount", "op": "gte", "value": 20},
                {"field": "id", "op": "in", "value": [2, 3]}]}})
        assert response.status_code == 200, response.text
        assert response.json()["rows"] == [{"id": 2, "amount": 20}]
