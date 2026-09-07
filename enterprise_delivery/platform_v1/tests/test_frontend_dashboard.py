from pathlib import Path


UI = (Path(__file__).parents[1] / "frontend" / "EnterpriseControlHub.jsx").read_text()


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
