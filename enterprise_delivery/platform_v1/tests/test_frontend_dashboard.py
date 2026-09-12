"""The old static mockup assertions are replaced by console browser tests.
Behavioral coverage lives in frontend/e2e/console.spec.js and test_console_api.py.
"""
from pathlib import Path


def test_public_product_metadata_keeps_canonical_title():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert "<title>SmartHub MCP & Agentic Gateway</title>" in html
