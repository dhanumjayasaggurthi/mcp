from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

MODULE_PATH = Path(__file__).parents[1] / "legacy_patch" / "data_api.py"


def _install_stubs() -> None:
    audit = types.ModuleType("audit")
    audit.write_audit_event = lambda **kwargs: None
    sys.modules["audit"] = audit

    auth = types.ModuleType("auth")
    auth.require_permission = lambda permission: (lambda: {"user": {"email": "tester@example.com"}})
    sys.modules["auth"] = auth

    dv = types.ModuleType("data_validation")
    dv.DEFAULT_ENVIRONMENT = "prod"
    dv._empty_or_none = lambda v: v or None
    dv._source_cfg = lambda source, env: {}
    dv._validate_identifier = lambda value, label: None
    dv.get_sources = lambda auth=None: {"sources": []}
    dv.get_source_databases = lambda **kwargs: {"applicable": True, "databases": []}
    dv.get_source_schemas = lambda **kwargs: {"applicable": True, "schemas": []}
    dv.get_source_tables = lambda **kwargs: {"tables": []}
    sys.modules["data_validation"] = dv

    connectors = types.ModuleType("data_validation_connectors")

    class ConnectorError(Exception):
        pass

    class DummyConnector:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_columns(self, database, schema, table):
            return [{"name": "id", "type": "INTEGER"}]

        def get_row_count(self, database, schema, table):
            return 0

        def fetch_rows(self, database, schema, table, columns, limit, order_by=None, offset=0):
            return []

        def search_rows(self, database, schema, table, columns, search, limit, order_by=None, offset=0, search_columns=None):
            return []

        def search_row_count(self, database, schema, table, search_columns, search):
            return 0

    connectors.ConnectorError = ConnectorError
    connectors.create_connector = lambda source, cfg: DummyConnector()
    sys.modules["data_validation_connectors"] = connectors


def load_module():
    _install_stubs()
    spec = importlib.util.spec_from_file_location("patched_data_api", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    # Clean DB so tests are deterministic.
    with module._db() as conn:
        conn.execute("DELETE FROM data_api_requests")
        conn.execute("DELETE FROM data_api_tables")
        conn.execute("DELETE FROM data_apis")
    return module


class RequestStub:
    def __init__(self, authorization: str = ""):
        self.headers = {"authorization": authorization} if authorization else {}
        self.client = types.SimpleNamespace(host="127.0.0.1")


def seed_api(module, api_id="api-1", slug="test-api", key="smarthub_secret"):
    with module._db() as conn:
        conn.execute(
            "INSERT INTO data_apis (id, slug, name, description, api_key, created_by, created_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (api_id, slug, "Test API", "", key, "tester", module._now().isoformat()),
        )


def test_list_does_not_return_live_api_keys():
    m = load_module()
    seed_api(m)
    payload = m.list_apis(auth={"user": {"email": "tester@example.com"}})
    assert len(payload["apis"]) == 1
    assert "api_key" not in payload["apis"][0]


def test_detailed_get_retains_legacy_key_for_no_downtime():
    m = load_module()
    seed_api(m)
    payload = m.get_api("test-api", auth={"user": {"email": "tester@example.com"}})
    assert payload["api_key"] == "smarthub_secret"


def test_bearer_header_is_accepted_and_preferred_over_query_key():
    m = load_module()
    seed_api(m)
    with m._db() as conn:
        row = m._authorize_key(
            conn,
            "test-api",
            "wrong-query-key",
            request=RequestStub("Bearer smarthub_secret"),
            endpoint="tables",
        )
    assert row["id"] == "api-1"


def test_legacy_query_key_still_works_during_migration():
    m = load_module()
    seed_api(m)
    with m._db() as conn:
        row = m._authorize_key(
            conn,
            "test-api",
            "smarthub_secret",
            request=RequestStub(),
            endpoint="tables",
        )
    assert row["id"] == "api-1"


def test_invalid_key_is_rejected():
    m = load_module()
    seed_api(m)
    with m._db() as conn:
        with pytest.raises(HTTPException) as exc:
            m._authorize_key(conn, "test-api", "bad", request=RequestStub(), endpoint="tables")
    assert exc.value.status_code == 401


def test_rows_do_not_run_exact_count_unless_explicitly_requested():
    m = load_module()
    seed_api(m)
    with m._db() as conn:
        conn.execute(
            "INSERT INTO data_api_tables (id, api_id, source_system, env, database_name, schema_name, table_name, alias, row_limit) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("t1", "api-1", "dummy", "prod", None, None, "records", "records", 100),
        )

    calls = {"count": 0}

    class CountingConnector:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def get_columns(self, database, schema, table): return [{"name": "id", "type": "INTEGER"}]
        def fetch_rows(self, database, schema, table, columns, limit, order_by=None, offset=0): return [{"id": 1}]
        def get_row_count(self, database, schema, table):
            calls["count"] += 1
            return 999999999

    m.create_connector = lambda source, cfg: CountingConnector()
    request = RequestStub("Bearer smarthub_secret")
    no_total = m.exec_get_rows("test-api", "records", request, api_key=None, limit=10, offset=0, search="", column="", include_total=False)
    assert no_total["total_rows"] is None
    assert calls["count"] == 0

    with_total = m.exec_get_rows("test-api", "records", request, api_key=None, limit=10, offset=0, search="", column="", include_total=True)
    assert with_total["total_rows"] == 999999999
    assert calls["count"] == 1
