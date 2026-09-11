from pathlib import Path


UI = (Path(__file__).parents[1] / "frontend" / "EnterpriseControlHub.jsx").read_text()
HTML = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
PRODUCT_NAME = "SmartHub MCP & Agentic Gateway"


def test_target_dashboard_sections_are_present():
    for label in [
        "Active Data Products", "Healthy Indexes", "Policy Violations", "Active Consumers",
        "P95 Latency", "Error Rate", "Deployment & Promotion", "Policy Enforcement",
        "Retrieval Services", "Index Health", "MCP Exposure", "Top Consumers",
        "Alerts / Guardrails", "Latest Audit Events",
    ]:
        assert label in UI


def test_target_navigation_and_environment_controls_are_present():
    for label in ["Audit", "Environments", "Access Control", "DEV", "QA", "PROD", "All Systems Operational"]:
        assert label in UI


def test_canonical_product_title_is_present_in_frontend_surfaces():
    assert PRODUCT_NAME in UI
    assert f"<title>{PRODUCT_NAME}</title>" in HTML
