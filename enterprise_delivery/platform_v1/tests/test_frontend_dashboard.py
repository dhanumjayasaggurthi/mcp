"""Production guard checks; functional UI coverage lives in Playwright."""
from pathlib import Path
ROOT=Path(__file__).parents[1]/'frontend'
UI=(ROOT/'EnterpriseControlHub.jsx').read_text()


def test_production_shell_has_no_fabricated_health_or_time():
    for old in ['Apr 24, 2025','All Systems Operational','Control plane connected','▂▃▄▅▆▇']:
        assert old not in UI
    assert 'SmartHub MCP & Agentic Gateway' in UI
    assert '<title>SmartHub MCP &amp; Agentic Gateway</title>' in (ROOT/'index.html').read_text() or '<title>SmartHub MCP & Agentic Gateway</title>' in (ROOT/'index.html').read_text()


def test_identity_adapter_never_persists_access_tokens():
    auth=(ROOT/'src/auth.js').read_text()
    assert 'InMemoryWebStorage' in auth
    assert 'response_type: "code"' in auth
    assert 'localStorage' not in auth
    assert 'Bearer ${token}' in (ROOT/'apiClient.js').read_text()
