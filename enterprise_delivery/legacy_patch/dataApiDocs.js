export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin;

export async function readJson(res) {
  return res.json().catch(() => ({}));
}

// Full, ready-to-paste endpoint URL for one table on one API.
export function tableUrl(apiId, alias) {
  return `${API_BASE_URL}/data-api/${apiId}/${alias}/rows?limit=100&offset=0`;
}

// Same table, but the browsable HTML view instead of raw JSON.
// Frontend-hosted React table view — separate origin from the API in dev,
// so this uses window.location.origin (the app), not API_BASE_URL (the API).
export function tableUiUrl(apiId, alias) {
  return `${window.location.origin}/data-api-view/${apiId}/${alias}`;
}

// Triggers a browser download of a text file — no backend round trip needed.
export function downloadTextFile(filename, content) {
  const blob = new Blob([content], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Consumer-facing doc, filled in with this API's real id/key/aliases so it's
// ready to hand off as-is — no placeholders for the admin to edit.
export function buildConsumerDoc(apiName, apiId, apiKey, tables) {
  const base = `${API_BASE_URL}/data-api/${apiId}`;
  const list = tables && tables.length ? tables : [];
  const first = list[0];
  const firstAlias = first ? first.alias : '{alias}';
  const tablesJsonExample = list.length
    ? list.map(t => `    { "id": "...", "alias": "${t.alias}", "source_system": "${t.source_system || '...'}", "env": "${t.env || '...'}", "database": ${t.database ? `"${t.database}"` : 'null'}, "schema": ${t.schema ? `"${t.schema}"` : 'null'}, "table": "${t.table || '...'}", "row_limit": ${t.row_limit ?? 1000} }`).join(',\n')
    : '    { "id": "...", "alias": "customers", "source_system": "...", "env": "...", "database": "...", "schema": "...", "table": "...", "row_limit": 1000 }';
  const tableListMd = list.length
    ? list.map(t => `| \`${t.alias}\` | ${t.source_system || ''} | ${t.row_limit ?? 1000} |`).join('\n')
    : '| *(none configured)* | | |';
  const rowsUrl = `${base}/${firstAlias}/rows?limit=500&offset=0`;
  const countUrl = `${base}/${firstAlias}/count`;
  const columnsUrl = `${base}/${firstAlias}/columns`;
  const tablesUrl = `${base}/tables`;
  const nextPageUrl = `${base}/${firstAlias}/rows?limit=500&offset=500`;

  return `# GRA-SmartHub - API Access Guide

You've been given access to this "${apiName}" API. No login needed — these are plain URLs, authenticated by the key below.

**API key:**
The API key will be shared separately in an encrypted format.

**Base URL:**
\`${base}\`

Keep the key private — treat it like a password. Send it in the \`Authorization: Bearer YOUR_API_KEY\` header. Do not place long-lived credentials in URLs. Wrong/missing key → \`401\`. If this API gets deactivated → \`403\`. The legacy \`api_key\` query parameter remains temporarily supported only for migration.

## Tables exposed on this API

| Alias | Source | Row limit/page |
|---|---|---|
${tableListMd}

Use the **alias** — not the real table/schema name — in every call below.

---

## 1. List available tables

\`\`\`
GET ${tablesUrl}
\`\`\`

Response:
\`\`\`json
{
  "api_id": "${apiId}",
  "tables": [
${tablesJsonExample}
  ]
}
\`\`\`

Use \`alias\` — that's the name you'll use in every other call, not the real table name.

---

## 2. Get columns for a table

\`\`\`
GET ${base}/${firstAlias}/columns
\`\`\`

Response:
\`\`\`json
{ "alias": "${firstAlias}", "columns": [ { "name": "id", "type": "INTEGER" }, ... ] }
\`\`\`

\`404\` if that alias doesn't exist on this API.

---

## 3. Get row count

\`\`\`
GET ${countUrl}&search=optional_term&column=optional_column_name
\`\`\`

Response:
\`\`\`json
{ "alias": "${firstAlias}", "total_rows": 5000, "max_offset": 4999, "row_limit": ${first?.row_limit ?? 1000} }
\`\`\`

\`search\` (optional) filters rows before counting. By default it checks every column — slow on large or wide tables. Pass \`column\` alongside it to restrict the search to one column instead, which is much faster since only that column gets scanned.

---

## 4. Get rows (the actual data)

\`\`\`
GET ${rowsUrl}&search=optional_term&column=optional_column_name
\`\`\`

Params:
- \`limit\` — rows per page. Capped automatically to this table's configured row limit (\`${first?.row_limit ?? 1000}\`) and a hard max of 10,000, whichever is smaller.
- \`offset\` — where to start. Default 0.
- \`search\` — optional, filters rows. Checks every column by default.
- \`column\` — optional, used together with \`search\`. Restricts the search to just this one column instead of every column — much faster on large tables, since the rest of the columns don't get scanned. Must be a real column name on this table (see section 2), or you'll get a \`400\`.
- \`include_total\` — optional boolean, default \`false\`. Set to \`true\` only when an exact first-page total is really required; on billion-row sources an exact count can be expensive.

Response:
\`\`\`json
{
  "alias": "${firstAlias}",
  "row_count": 500,
  "total_rows": 5000,
  "max_offset": 4999,
  "offset": 0,
  "limit": 500,
  "has_more": true,
  "next_offset": 500,
  "search": null,
  "column": null,
  "rows": [ { "id": 1, "...": "..." }, ... ]
}
\`\`\`

### Pagination

- Rows come back in a stable order every time, so paging is safe.
- If \`has_more\` is \`true\`, call again with \`offset = next_offset\`.
- Keep going until \`has_more\` is \`false\`.
- \`total_rows\` / \`max_offset\` are \`null\` by default. Request \`include_total=true\` on the first page or call the explicit count endpoint only when a total is required. Keep using \`has_more\`/\`next_offset\` for normal paging.
- There's no limit on how big the table can be overall — only on how many rows come back per page.

### Search

Add \`search=term\` to \`count\` or \`rows\` to filter rows. Same param, same behavior in both endpoints — count it first if you want to know how many pages to expect, or just page through \`rows\` until \`has_more\` is \`false\`.

By default \`search\` matches across every column, which means every column gets cast and scanned — on large tables (millions/billions of rows, or tables with big text columns) this can be slow. If you know which column has what you're looking for, add \`column=column_name\` alongside \`search\` to check only that column. Much faster, and works the same way in both \`count\` and \`rows\`.

---

## Errors

| Status | Meaning |
|---|---|
| 401 | api_key missing or wrong |
| 403 | this API has been deactivated — contact whoever gave you the key |
| 404 | alias doesn't exist on this API |
| 502 | source system couldn't be reached — retry later |

---

## Quick example (curl)

\`\`\`bash
curl -H "Authorization: Bearer YOUR_API_KEY" "${tablesUrl}"
curl -H "Authorization: Bearer YOUR_API_KEY" "${columnsUrl}"
curl -H "Authorization: Bearer YOUR_API_KEY" "${rowsUrl}"
curl -H "Authorization: Bearer YOUR_API_KEY" "${nextPageUrl}"
\`\`\`

That's it — 4 endpoints, one Bearer credential, plain GET requests. Use the returned next_offset when paging; never assume the server honored the requested limit.
`;
}
