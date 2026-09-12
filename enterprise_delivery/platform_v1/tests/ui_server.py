"""Isolated browser-test composition; never imported by a production entrypoint."""
import tempfile
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine

from enterprise_data_platform.api import create_app, _register_registry_routes
from enterprise_data_platform.connectors import SourceRegistration
from enterprise_data_platform.durable import RelationalStore, migrate
from enterprise_data_platform.onboarding import register_onboarding
from enterprise_data_platform.observability import Metrics
from enterprise_data_platform.production_app import SQLOperationsProvider
from test_production_boundaries import runtime_service


def build_console_app(store):
    service, actor = runtime_service(store)
    service.metrics = Metrics()
    def resolver(request):
        token = request.headers.get("authorization")
        if token not in {"Bearer ui-test-token", "Bearer ui-reader-token"}:
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
        return actor.model_copy(update={
            "groups": {"data-platform-admin"} if token == "Bearer ui-test-token" else set(),
            "attributes": {"oauth_scope": "edp:admin edp:query edp:aggregate edp:exact_count"}})
    app = create_app(service=service, catalog=service.catalog, policies=service.policies,
        control_state=service.control, principal_resolver=resolver,
        control_admin_check=lambda p: "data-platform-admin" in p.groups,
        operations_provider=SQLOperationsProvider(store), readiness_check=store.ready, runtime_mode="test")
    runtime = SimpleNamespace(router=service.structured)
    register_onboarding(app, runtime)
    _register_registry_routes(app, "sources", runtime.router.registry, SourceRegistration, app.state.admin_dependency)
    app.state.test_service = service
    return app


def create_test_app():
    directory = tempfile.TemporaryDirectory(prefix="smarthub-console-test-")
    engine = create_engine("sqlite:///" + directory.name + "/control.db", connect_args={"timeout": 5})
    migrate(engine)
    app = build_console_app(RelationalStore(engine, production=False))
    app.state.test_directory = directory
    return app
