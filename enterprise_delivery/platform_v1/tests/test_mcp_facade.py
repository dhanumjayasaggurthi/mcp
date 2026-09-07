import pytest

from enterprise_data_platform.control_models import AgentRegistration
from enterprise_data_platform.control_state import ControlState
from enterprise_data_platform.mcp_facade import MCPFacade
from enterprise_data_platform.models import Capability, Principal
from enterprise_data_platform.services import AccessDenied
from test_platform_core import build_service, principal


def test_mcp_facade_reuses_platform_policies_and_agent_scope():
    service, catalog, _ = build_service()
    control = ControlState()
    control.agents.put(
        AgentRegistration(
            id="regassist-agent",
            display_name="RegAssist",
            owner="regulatory-ai",
            service_principal="svc-regassist",
            allowed_datasets={"regulatory-docs"},
            allowed_capabilities={Capability.RETRIEVE, Capability.HYBRID, Capability.KEYWORD},
            require_citations=True,
            max_top_k=2,
            max_context_chars=10000,
            mcp_enabled=True,
        )
    )
    facade = MCPFacade(service=service, catalog=catalog, control=control)
    result = facade.invoke(
        agent_id="regassist-agent",
        principal=principal(),
        tool="retrieve_context",
        arguments={"dataset_id": "regulatory-docs", "query": "adverse event", "top_k": 999, "mode": "hybrid"},
    )
    assert 0 < len(result["results"]) <= 2
    assert all(r["source"]["dataset"] == "regulatory-docs" for r in result["results"])


def test_mcp_cannot_impersonate_registered_agent():
    service, catalog, _ = build_service()
    control = ControlState()
    control.agents.put(
        AgentRegistration(
            id="a",
            display_name="A",
            owner="o",
            service_principal="svc-regassist",
            allowed_datasets={"regulatory-docs"},
            allowed_capabilities={Capability.RETRIEVE},
            mcp_enabled=True,
        )
    )
    facade = MCPFacade(service=service, catalog=catalog, control=control)
    bad = Principal(subject="other-service", client_id="regassist-prod", tenant="acme", groups={"rag-consumers"})
    with pytest.raises(AccessDenied, match="principal does not match"):
        facade.invoke(agent_id="a", principal=bad, tool="retrieve_context", arguments={"dataset_id": "regulatory-docs", "query": "x"})
