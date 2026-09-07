from pathlib import Path
import subprocess

ROOT = Path(__file__).parents[1] / "legacy_patch"


def test_consumer_docs_do_not_embed_live_key_in_urls():
    text = (ROOT / "dataApiDocs.js").read_text()
    assert "?api_key=" not in text
    assert "Authorization: Bearer YOUR_API_KEY" in text


def test_table_view_uses_authorization_header_and_server_next_offset():
    text = (ROOT / "DataApiTableView.jsx").read_text()
    assert "headers: { Authorization: `Bearer ${apiKey}` }" in text
    assert "off += chunk" not in text
    assert "off = serverNext" in text
    assert "setNextOffset(data.next_offset ?? null)" in text
    assert "if (nextOffset != null) setOffset(nextOffset)" in text
    assert "BROWSER_EXPORT_MAX_ROWS = 50_000" in text
    assert "managed async export service" in text


def test_management_console_no_long_lived_keys_in_query_strings():
    text = (ROOT / "DataAPIExport.jsx").read_text()
    assert "?api_key=" not in text
    assert "Authorization: Bearer" in text
    assert "Copy cURL" in text


def test_jsx_files_parse_with_typescript_parser():
    for name in ["DataApiTableView.jsx", "DataAPIExport.jsx", "DataTable.jsx"]:
        subprocess.run(
            [
                "tsc",
                "--allowJs",
                "--checkJs",
                "false",
                "--jsx",
                "react",
                "--noEmit",
                "--skipLibCheck",
                str(ROOT / name),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
