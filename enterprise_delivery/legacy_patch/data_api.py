from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import time
import uuid

import pytz
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from audit import write_audit_event
from auth import require_permission
from data_validation import (
    DEFAULT_ENVIRONMENT,
    _empty_or_none,
    _source_cfg,
    _validate_identifier,
    get_sources as _dv_get_sources,
    get_source_databases as _dv_get_source_databases,
    get_source_schemas as _dv_get_source_schemas,
    get_source_tables as _dv_get_source_tables,
)
from data_validation_connectors import ConnectorError, create_connector

logger = logging.getLogger(__name__)

data_api_router = APIRouter(prefix="/data-api", tags=["Data API Export"])

DB_PATH = Path(__file__).parent / "data_api_exports.db"

MAX_ROW_LIMIT = 10_000
DEFAULT_ROW_LIMIT = 1_000

# The audit log lives in its own SQLite file next to this one. The activity feed
# reads it directly, read-only, so this module doesn't need a new API surface in
# audit.py just to list one API's management events.
AUDIT_DB_PATH = Path(__file__).parent / "audit_log.db"

# Every timestamp this module writes or reports is Eastern, matching audit.py.
# It also means a day bucket is an Eastern calendar day, so a 9pm ET call lands
# on the day the caller made it rather than the next UTC one.
EASTERN = pytz.timezone("America/New_York")


def _now() -> datetime:
    return datetime.now(EASTERN)

# Every consumer call is logged as one row in data_api_requests. 400 days keeps a
# full year of history for the usage chart without letting the table grow forever.
REQUEST_LOG_RETENTION_DAYS = 400


def _resolve_search_columns(column: str, all_columns: List[str]) -> List[str]:
    """Given the optional `column` query param and the table's real column
    list, returns the column(s) to search: just `column` if given (after
    validating it's real — 400 otherwise), or every column if not. Shared by
    exec_get_count and exec_get_rows so the validation only lives in one
    place."""
    if not column:
        return all_columns
    if column not in all_columns:
        raise HTTPException(status_code=400, detail=f"No column '{column}' on this table.")
    return [column]


# ============================================================================
# DATABASE SETUP
# ============================================================================

def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


@contextmanager
def _db():
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _new_api_key() -> str:
    return f"smarthub_{secrets.token_urlsafe(32)}"


# ============================================================================
# SLUGS — friendly, url-safe stand-ins for the raw uuid api_id. Generated once
# at creation from the name and stored; renaming the API does not reslug it,
# so URLs already shared keep working.
# ============================================================================

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "api"


def _unique_slug(conn: sqlite3.Connection, name: str, exclude_id: Optional[str] = None) -> str:
    base = _slugify(name)
    slug = base
    n = 2
    while True:
        row = conn.execute("SELECT id FROM data_apis WHERE slug = ?", (slug,)).fetchone()
        if not row or row["id"] == exclude_id:
            return slug
        slug = f"{base}-{n}"
        n += 1


def _init_db() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS data_apis (
                id TEXT PRIMARY KEY,
                slug TEXT UNIQUE,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                api_key TEXT NOT NULL UNIQUE,
                created_by TEXT,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_used_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS data_api_tables (
                id TEXT PRIMARY KEY,
                api_id TEXT NOT NULL,
                source_system TEXT NOT NULL,
                env TEXT NOT NULL,
                database_name TEXT,
                schema_name TEXT,
                table_name TEXT NOT NULL,
                alias TEXT NOT NULL,
                row_limit INTEGER NOT NULL DEFAULT 1000,
                FOREIGN KEY (api_id) REFERENCES data_apis(id)
            )
        """)
        # One row per consumer call. This is the only place request history
        # exists: the source tables are never written to, and last_used_at on
        # data_apis is a single timestamp, so usage over time has to live here.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS data_api_requests (
                id TEXT PRIMARY KEY,
                api_id TEXT NOT NULL,
                alias TEXT,
                endpoint TEXT NOT NULL,
                ts TEXT NOT NULL,
                ip TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                status INTEGER NOT NULL DEFAULT 200,
                row_count INTEGER,
                duration_ms INTEGER,
                FOREIGN KEY (api_id) REFERENCES data_apis(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_data_api_requests_api_ts
            ON data_api_requests (api_id, ts DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_data_api_requests_ts
            ON data_api_requests (ts DESC)
        """)
        # Migrate an older hash-only install (key wasn't recoverable there —
        # those rows get a freshly generated key so the URL can be shown again).
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(data_apis)").fetchall()}
        if "api_key" not in cols:
            conn.execute("ALTER TABLE data_apis ADD COLUMN api_key TEXT")
            for row in conn.execute("SELECT id FROM data_apis WHERE api_key IS NULL").fetchall():
                conn.execute("UPDATE data_apis SET api_key = ? WHERE id = ?", (_new_api_key(), row["id"]))
        # Migrate an install from before slugs existed — backfill one per
        # row from its current name so old APIs get friendly URLs too.
        if "slug" not in cols:
            conn.execute("ALTER TABLE data_apis ADD COLUMN slug TEXT")
            for row in conn.execute("SELECT id, name FROM data_apis WHERE slug IS NULL").fetchall():
                conn.execute(
                    "UPDATE data_apis SET slug = ? WHERE id = ?",
                    (_unique_slug(conn, row["name"], exclude_id=row["id"]), row["id"]),
                )


_init_db()


def _purge_old_requests() -> None:
    """Drop request-log rows past the retention window. Cheap, and called from
    startup plus the admin-only usage endpoint rather than on every consumer
    call."""
    cutoff = (_now() - timedelta(days=REQUEST_LOG_RETENTION_DAYS)).isoformat()
    try:
        with _db() as conn:
            conn.execute("DELETE FROM data_api_requests WHERE ts < ?", (cutoff,))
    except Exception as e:
        logger.warning("request-log purge failed: %s", e)


_purge_old_requests()


# ============================================================================
# HELPERS
# ============================================================================

def _table_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "source_system": r["source_system"],
        "env": r["env"],
        "database": r["database_name"],
        "schema": r["schema_name"],
        "table": r["table_name"],
        "alias": r["alias"],
        "row_limit": r["row_limit"],
    }


def _api_row(
    r: sqlite3.Row,
    tables: Optional[List[dict]] = None,
    *,
    include_key: bool = False,
) -> dict:
    """Serialize an API definition.

    Long-lived credentials are excluded by default so list/usage-style endpoints
    cannot accidentally leak every consumer secret. Detailed management flows
    that intentionally need the live key must opt in explicitly.
    """
    out = {
        "id": r["id"],
        "slug": r["slug"],
        "name": r["name"],
        "description": r["description"],
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "is_active": bool(r["is_active"]),
        "last_used_at": r["last_used_at"],
    }
    if include_key:
        out["api_key"] = r["api_key"]
    if tables is not None:
        out["tables"] = tables
    return out


def _get_api(conn: sqlite3.Connection, api_id: str) -> sqlite3.Row:
    """Look up an API by its uuid id OR its friendly slug — either works as
    the {api_id} path segment, so old raw-uuid links never break."""
    row = conn.execute("SELECT * FROM data_apis WHERE id = ? OR slug = ?", (api_id, api_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No API found with that ID.")
    return row


def _get_tables(conn: sqlite3.Connection, api_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM data_api_tables WHERE api_id = ? ORDER BY alias", (api_id,)
    ).fetchall()


# ============================================================================
# REQUEST LOGGING — consumer calls are the only source of usage data, so every
# one of them, including rejected ones, gets a row here.
# ============================================================================

def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return getattr(getattr(request, "client", None), "host", "") or ""


def _log_request(
    api_id: str,
    endpoint: str,
    alias: Optional[str] = None,
    request: Optional[Request] = None,
    status: int = 200,
    row_count: Optional[int] = None,
    duration_ms: Optional[int] = None,
    mark_used: bool = True,
) -> None:
    """Record one consumer call. Never raises: a logging failure must not turn a
    working data pull into a 500."""
    try:
        now = _now().isoformat()
        with _db() as conn:
            conn.execute(
                "INSERT INTO data_api_requests (id, api_id, alias, endpoint, ts, ip, user_agent, "
                "status, row_count, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), api_id, alias, endpoint, now,
                    _client_ip(request),
                    ((request.headers.get("user-agent") if request else "") or "")[:300],
                    int(status), row_count, duration_ms,
                ),
            )
            if mark_used and 200 <= int(status) < 300:
                conn.execute("UPDATE data_apis SET last_used_at = ? WHERE id = ?", (now, api_id))
    except Exception as e:
        logger.warning("data-api request logging failed: %s", e)


def _iso_ts(ts: str) -> str:
    """Rows written by this module carry an Eastern offset already. Rows written
    before that change are naive UTC, so tag those with Z rather than letting a
    reader guess."""
    if not ts:
        return ts
    return ts if (ts.endswith("Z") or "+" in ts[10:] or "-" in ts[10:]) else ts + "Z"


def _sort_key(ts: str) -> datetime:
    """Offset-aware and legacy naive rows both have to land on one timeline
    before the merged feed can be sorted."""
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _read_audit_events(api_id: str, name: str, limit: int = 40) -> List[dict]:
    """Management events for one API, read straight out of the audit log DB.
    Matches on the api_id stamped into the details column (written by this
    module) and on the API's current name in target, which covers rows written
    before that stamp existed."""
    if not AUDIT_DB_PATH.exists():
        return []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{AUDIT_DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT timestamp, action, performed_by, target, details, ip_address FROM audit_log "
            "WHERE action LIKE 'data_api%' AND (details LIKE ? OR target = ?) "
            "ORDER BY timestamp DESC LIMIT ?",
            (f"%api_id={api_id}%", name or "", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("could not read audit events for data-api %s: %s", api_id, e)
        return []
    finally:
        if conn is not None:
            conn.close()


class TableSpec(BaseModel):
    source_system: str
    env: str = DEFAULT_ENVIRONMENT
    database: Optional[str] = None
    db_schema: Optional[str] = None
    table: str
    alias: str
    row_limit: int = DEFAULT_ROW_LIMIT


class CreateAPIRequest(BaseModel):
    name: str
    description: str = ""
    tables: List[TableSpec]


class UpdateAPIRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    tables: Optional[List[TableSpec]] = None


def _validate_tables(tables: List[TableSpec]) -> None:
    if not tables:
        raise HTTPException(status_code=400, detail="At least one table is required.")
    seen_aliases = set()
    for t in tables:
        alias = (t.alias or "").strip()
        if not alias:
            raise HTTPException(status_code=400, detail="Every table needs an alias.")
        if not alias.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(status_code=400, detail=f"Alias '{alias}' can only contain letters, numbers, _ and -.")
        if alias.lower() in seen_aliases:
            raise HTTPException(status_code=400, detail=f"Alias '{alias}' is used more than once — aliases must be unique within an API.")
        seen_aliases.add(alias.lower())
        _validate_identifier(t.database, "database")
        _validate_identifier(t.db_schema, "schema")
        _validate_identifier(t.table, "table")
        if t.row_limit < 1 or t.row_limit > MAX_ROW_LIMIT:
            raise HTTPException(status_code=400, detail=f"row_limit must be between 1 and {MAX_ROW_LIMIT}.")
        # Fail fast if the table doesn't actually exist / isn't reachable.
        cfg = _source_cfg(t.source_system, t.env)
        try:
            with create_connector(t.source_system, cfg) as conn:
                conn.get_columns(_empty_or_none(t.database), _empty_or_none(t.db_schema), t.table)
        except ConnectorError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read '{t.table}': {e}")


# ============================================================================
# TABLE PICKER — thin proxies onto the Data Validation source browser so this
# page doesn't need its own copy of the connector-listing logic.
# ============================================================================

@data_api_router.get("/sources")
def list_sources(auth: dict = Depends(require_permission("manageDataAPIExports"))):
    """List source systems available to build a Data API Export from — proxies the
    Data Validation source picker so this feature doesn't duplicate that logic."""
    return _dv_get_sources(auth=auth)


@data_api_router.get("/source-databases")
def list_source_databases(
    source_system: str = Query(...),
    env: str = Query(DEFAULT_ENVIRONMENT),
    auth: dict = Depends(require_permission("manageDataAPIExports")),
):
    """List databases available on a given source system/environment, for the
    Data API Export table picker."""
    return _dv_get_source_databases(source_system=source_system, env=env, auth=auth)


@data_api_router.get("/source-schemas")
def list_source_schemas(
    source_system: str = Query(...),
    database: Optional[str] = Query(None),
    env: str = Query(DEFAULT_ENVIRONMENT),
    auth: dict = Depends(require_permission("manageDataAPIExports")),
):
    """List schemas within a source database, for the Data API Export table picker."""
    return _dv_get_source_schemas(source_system=source_system, database=database, env=env, auth=auth)


@data_api_router.get("/source-tables")
def list_source_tables(
    source_system: str = Query(...),
    database: Optional[str] = Query(None),
    schema: Optional[str] = Query(None),
    env: str = Query(DEFAULT_ENVIRONMENT),
    auth: dict = Depends(require_permission("manageDataAPIExports")),
):
    """List tables within a source schema, for the Data API Export table picker."""
    return _dv_get_source_tables(source_system=source_system, database=database, schema=schema, env=env, auth=auth)


# ============================================================================
# MANAGEMENT — create / list / edit / delete API definitions
# ============================================================================

@data_api_router.get("/list")
def list_apis(auth: dict = Depends(require_permission("manageDataAPIExports"))):
    """List every Data API Export definition (name, description, active status,
    table count, calls in the last 7 days) most recently created first. Does not
    include the api_key — call get_api for one specific API to get its key."""
    since_7d = (_now() - timedelta(days=6)).date().isoformat()
    with _db() as conn:
        rows = conn.execute("SELECT * FROM data_apis ORDER BY created_at DESC").fetchall()
        out = []
        for r in rows:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM data_api_tables WHERE api_id = ?", (r["id"],)
            ).fetchone()["n"]
            calls = conn.execute(
                "SELECT COUNT(*) AS n FROM data_api_requests WHERE api_id = ? AND ts >= ?",
                (r["id"], since_7d),
            ).fetchone()["n"]
            item = _api_row(r)
            item["table_count"] = count
            item["requests_7d"] = calls
            out.append(item)
    return {"apis": out}


@data_api_router.post("/create")
def create_api(body: CreateAPIRequest, auth: dict = Depends(require_permission("manageDataAPIExports"))):
    """Create a new Data API Export: a named, key-authenticated bundle of one or
    more source tables (each given an alias and a per-request row_limit). Each
    table is checked for reachability before creation. Returns the new api_key —
    shown only here and on the page; it can be regenerated later if lost/rotated."""
    _validate_tables(body.tables)
    api_id = str(uuid.uuid4())
    key = _new_api_key()
    now = _now().isoformat()
    creator = auth.get("user", {}).get("email") or auth.get("user", {}).get("name") or "unknown"

    with _db() as conn:
        slug = _unique_slug(conn, body.name.strip())
        conn.execute(
            "INSERT INTO data_apis (id, slug, name, description, api_key, created_by, created_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (api_id, slug, body.name.strip(), body.description.strip(), key, creator, now),
        )
        for t in body.tables:
            conn.execute(
                "INSERT INTO data_api_tables (id, api_id, source_system, env, database_name, schema_name, "
                "table_name, alias, row_limit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), api_id, t.source_system, t.env, t.database, t.db_schema, t.table, t.alias.strip(), t.row_limit),
            )
        row = _get_api(conn, api_id)
        tables = [_table_row(t) for t in _get_tables(conn, api_id)]

    write_audit_event(
        action="data_api_created",
        performed_by=creator,
        target=body.name.strip(),
        details=f"Created API '{body.name.strip()}' with {len(body.tables)} table(s) (api_id={api_id})",
    )

    return _api_row(row, tables, include_key=True)


@data_api_router.get("/{api_id}")
def get_api(api_id: str, auth: dict = Depends(require_permission("manageDataAPIExports"))):
    """Full detail for one Data API Export, including its tables and its live api_key."""
    with _db() as conn:
        row = _get_api(conn, api_id)
        tables = [_table_row(t) for t in _get_tables(conn, row["id"])]
    return _api_row(row, tables, include_key=True)


@data_api_router.get("/{api_id}/usage")
def get_api_usage(
    api_id: str,
    days: int = Query(14, ge=1, le=365),
    auth: dict = Depends(require_permission("manageDataAPIExports")),
):
    """Real call history for one Data API Export, straight from the request log:
    one bucket per day (including days with no calls), totals, a breakdown by
    endpoint and alias, and the distinct callers seen in the window. Days are
    Eastern calendar days. Everything here comes from consumer calls actually
    served — an API nobody has called reports zeroes, not estimates."""
    _purge_old_requests()
    since_date = (_now() - timedelta(days=days - 1)).date().isoformat()

    with _db() as conn:
        row = _get_api(conn, api_id)
        real_id = row["id"]

        daily = conn.execute(
            "SELECT substr(ts, 1, 10) AS d, COUNT(*) AS calls, "
            "COALESCE(SUM(row_count), 0) AS rows_served "
            "FROM data_api_requests WHERE api_id = ? AND substr(ts, 1, 10) >= ? "
            "GROUP BY d",
            (real_id, since_date),
        ).fetchall()

        totals = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(row_count), 0) AS rows_served, "
            "SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors, "
            "MIN(ts) AS first_call, MAX(ts) AS last_call "
            "FROM data_api_requests WHERE api_id = ? AND substr(ts, 1, 10) >= ?",
            (real_id, since_date),
        ).fetchone()

        lifetime = conn.execute(
            "SELECT COUNT(*) AS calls, MIN(ts) AS first_call FROM data_api_requests WHERE api_id = ?",
            (real_id,),
        ).fetchone()

        by_endpoint = conn.execute(
            "SELECT endpoint, COUNT(*) AS calls FROM data_api_requests "
            "WHERE api_id = ? AND substr(ts, 1, 10) >= ? GROUP BY endpoint ORDER BY calls DESC",
            (real_id, since_date),
        ).fetchall()

        by_alias = conn.execute(
            "SELECT alias, COUNT(*) AS calls, COALESCE(SUM(row_count), 0) AS rows_served "
            "FROM data_api_requests WHERE api_id = ? AND substr(ts, 1, 10) >= ? AND alias IS NOT NULL "
            "GROUP BY alias ORDER BY calls DESC",
            (real_id, since_date),
        ).fetchall()

        # A caller is a source IP: the key is the only credential these endpoints
        # take, so the host is the closest thing to an identity. The per-agent
        # split below is kept as detail on the caller rather than as separate
        # callers, so one machine driving two clients counts once. (Collapsing in
        # SQL with MAX(user_agent) would report the lexicographically largest
        # agent string instead of the one that actually called last.)
        agent_rows = conn.execute(
            "SELECT COALESCE(NULLIF(ip, ''), 'unknown') AS ip, "
            "COALESCE(user_agent, '') AS user_agent, COUNT(*) AS calls, "
            "COALESCE(SUM(row_count), 0) AS rows_served, MAX(ts) AS last_call "
            "FROM data_api_requests WHERE api_id = ? AND substr(ts, 1, 10) >= ? "
            "GROUP BY COALESCE(NULLIF(ip, ''), 'unknown'), COALESCE(user_agent, '') "
            "ORDER BY calls DESC LIMIT 60",
            (real_id, since_date),
        ).fetchall()

        avg_ms = conn.execute(
            "SELECT AVG(duration_ms) AS ms FROM data_api_requests "
            "WHERE api_id = ? AND substr(ts, 1, 10) >= ? AND duration_ms IS NOT NULL",
            (real_id, since_date),
        ).fetchone()["ms"]

    by_ip: Dict[str, dict] = {}
    for r in agent_rows:
        entry = by_ip.setdefault(r["ip"], {
            "ip": r["ip"], "calls": 0, "rows_served": 0, "last_call": None, "agents": [],
        })
        entry["calls"] += r["calls"]
        entry["rows_served"] += r["rows_served"]
        if entry["last_call"] is None or r["last_call"] > entry["last_call"]:
            entry["last_call"] = r["last_call"]
        if r["user_agent"]:
            entry["agents"].append({
                "user_agent": r["user_agent"],
                "calls": r["calls"],
                "rows_served": r["rows_served"],
                "last_call": _iso_ts(r["last_call"]),
            })

    callers = sorted(by_ip.values(), key=lambda c: c["calls"], reverse=True)[:10]
    for c in callers:
        # Most recently used client first — that is the one worth showing when
        # there is only room for a short summary.
        c["agents"].sort(key=lambda a: a["last_call"], reverse=True)
        c["user_agent"] = c["agents"][0]["user_agent"] if c["agents"] else ""
        c["last_call"] = _iso_ts(c["last_call"])

    seen = {r["d"]: r for r in daily}
    buckets = []
    for offset_days in range(days - 1, -1, -1):
        day = (_now() - timedelta(days=offset_days)).date().isoformat()
        hit = seen.get(day)
        buckets.append({
            "date": day,
            "calls": hit["calls"] if hit else 0,
            "rows_served": hit["rows_served"] if hit else 0,
        })

    return {
        "api_id": real_id,
        "days": days,
        "buckets": buckets,
        "total_calls": totals["calls"] or 0,
        "total_rows_served": totals["rows_served"] or 0,
        "errors": totals["errors"] or 0,
        "first_call": _iso_ts(totals["first_call"]) if totals["first_call"] else None,
        "last_call": _iso_ts(totals["last_call"]) if totals["last_call"] else None,
        "lifetime_calls": lifetime["calls"] or 0,
        "logging_since": _iso_ts(lifetime["first_call"]) if lifetime["first_call"] else None,
        "avg_duration_ms": int(avg_ms) if avg_ms is not None else None,
        "by_endpoint": [dict(r) for r in by_endpoint],
        "by_alias": [dict(r) for r in by_alias],
        "callers": callers,
    }


_ACTION_PHRASES = {
    "data_api_created": "API created",
    "data_api_updated": "Configuration changed",
    "data_api_key_regenerated": "API key rotated — previous key revoked",
    "data_api_deleted": "API deleted",
}


@data_api_router.get("/{api_id}/activity")
def get_api_activity(
    api_id: str,
    limit: int = Query(40, ge=1, le=200),
    auth: dict = Depends(require_permission("manageDataAPIExports")),
):
    """One merged, newest-first feed for an API: management events from the audit
    log (created, edited, key rotated) and consumer calls from the request log,
    on a single timeline."""
    with _db() as conn:
        row = _get_api(conn, api_id)
        real_id = row["id"]
        calls = conn.execute(
            "SELECT ts, endpoint, alias, ip, status, row_count, user_agent "
            "FROM data_api_requests WHERE api_id = ? ORDER BY ts DESC LIMIT ?",
            (real_id, limit),
        ).fetchall()

    events = []
    for r in calls:
        target = f"/{r['alias']}" if r["alias"] else "/tables"
        if r["status"] >= 400:
            what = f"Call to {target} rejected — HTTP {r['status']}"
        elif r["row_count"] is not None and r["endpoint"] == "rows":
            what = f"{r['row_count']:,} rows served from {target}"
        else:
            what = f"{r['endpoint']} read on {target}"
        events.append({
            "ts": _iso_ts(r["ts"]),
            "kind": "request",
            "action": r["endpoint"],
            "status": r["status"],
            "what": what,
            "who": f"key holder · {r['ip'] or 'unknown IP'}",
        })

    for a in _read_audit_events(real_id, row["name"], limit=limit):
        events.append({
            "ts": a["timestamp"],
            "kind": "management",
            "action": a["action"],
            "status": 200,
            "what": _ACTION_PHRASES.get(a["action"], a["action"].replace("_", " ")),
            "who": a["performed_by"] or "unknown",
            "details": a["details"] or "",
        })

    events.sort(key=lambda e: _sort_key(e["ts"]), reverse=True)
    return {"api_id": real_id, "events": events[:limit]}


@data_api_router.patch("/{api_id}")
def update_api(api_id: str, body: UpdateAPIRequest, auth: dict = Depends(require_permission("manageDataAPIExports"))):
    """Partially update a Data API Export: rename it, edit its description,
    activate/deactivate it, or replace its whole table list (a table list
    replaces every existing table for this API, it doesn't merge). Renaming
    does not change the slug already baked into shared URLs."""
    if body.tables is not None:
        _validate_tables(body.tables)
    editor = auth.get("user", {}).get("email") or auth.get("user", {}).get("name") or "unknown"
    changed = []
    with _db() as conn:
        existing = _get_api(conn, api_id)  # 404 if missing
        real_id = existing["id"]
        if body.name is not None:
            conn.execute("UPDATE data_apis SET name = ? WHERE id = ?", (body.name.strip(), real_id))
            changed.append("name")
        if body.description is not None:
            conn.execute("UPDATE data_apis SET description = ? WHERE id = ?", (body.description.strip(), real_id))
            changed.append("description")
        if body.is_active is not None:
            conn.execute("UPDATE data_apis SET is_active = ? WHERE id = ?", (1 if body.is_active else 0, real_id))
            changed.append("activated" if body.is_active else "deactivated")
        if body.tables is not None:
            conn.execute("DELETE FROM data_api_tables WHERE api_id = ?", (real_id,))
            for t in body.tables:
                conn.execute(
                    "INSERT INTO data_api_tables (id, api_id, source_system, env, database_name, schema_name, "
                    "table_name, alias, row_limit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), real_id, t.source_system, t.env, t.database, t.db_schema, t.table, t.alias.strip(), t.row_limit),
                )
            changed.append("tables")
        row = _get_api(conn, real_id)
        tables = [_table_row(t) for t in _get_tables(conn, real_id)]

    write_audit_event(
        action="data_api_updated",
        performed_by=editor,
        target=row["name"] or existing["name"],
        details=f"Updated: {', '.join(changed) if changed else 'no fields changed'} (api_id={real_id})",
    )

    return _api_row(row, tables, include_key=True)


@data_api_router.post("/{api_id}/regenerate-key")
def regenerate_key(api_id: str, auth: dict = Depends(require_permission("manageDataAPIExports"))):
    """Issue a brand new api_key for this Data API Export and immediately revoke
    the old one — any URL built with the previous key stops working."""
    key = _new_api_key()
    performer = auth.get("user", {}).get("email") or auth.get("user", {}).get("name") or "unknown"
    with _db() as conn:
        row = _get_api(conn, api_id)
        conn.execute(
            "UPDATE data_apis SET api_key = ? WHERE id = ?",
            (key, row["id"]),
        )

    write_audit_event(
        action="data_api_key_regenerated",
        performed_by=performer,
        target=row["name"],
        details=f"API key regenerated — previous key revoked (api_id={row['id']})",
    )

    return {"id": row["id"], "slug": row["slug"], "api_key": key}


@data_api_router.delete("/{api_id}")
def delete_api(api_id: str, auth: dict = Depends(require_permission("manageDataAPIExports"))):
    """Permanently delete a Data API Export and every table entry on it. Its
    api_key stops working immediately; this cannot be undone."""
    performer = auth.get("user", {}).get("email") or auth.get("user", {}).get("name") or "unknown"
    with _db() as conn:
        row = _get_api(conn, api_id)
        conn.execute("DELETE FROM data_api_tables WHERE api_id = ?", (row["id"],))
        conn.execute("DELETE FROM data_api_requests WHERE api_id = ?", (row["id"],))
        conn.execute("DELETE FROM data_apis WHERE id = ?", (row["id"],))

    write_audit_event(
        action="data_api_deleted",
        performed_by=performer,
        target=row["name"],
        details=f"Deleted API '{row['name']}' (api_id={row['id']})",
    )

    return {"deleted": True}


# ============================================================================
# EXECUTE — consumer data-plane endpoints.
#
# Preferred authentication is ``Authorization: Bearer <api-key>`` so secrets do
# not appear in URLs, browser history, reverse-proxy query logs, or referrers.
# The legacy ``api_key`` query parameter remains accepted during migration to
# avoid downtime for existing consumers.
# ============================================================================

def _consumer_credential(request: Optional[Request], legacy_api_key: Optional[str]) -> str:
    """Return the consumer credential, preferring the Authorization header.

    This is intentionally small and protocol-agnostic: OAuth/workload identity
    belongs in the v1 gateway. The legacy API keeps accepting its existing
    secret while allowing clients to migrate off query-string credentials.
    """
    if request is not None:
        authorization = (request.headers.get("authorization") or "").strip()
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    return (legacy_api_key or "").strip()


def _authorize_key(
    conn: sqlite3.Connection,
    api_id: str,
    api_key: Optional[str],
    request: Optional[Request] = None,
    endpoint: str = "",
    alias: Optional[str] = None,
) -> sqlite3.Row:
    """Rejected calls are logged too — a spike of 401s on a key is exactly what
    the owner needs to see on the Activity tab."""
    row = _get_api(conn, api_id)
    if not row["is_active"]:
        _log_request(row["id"], endpoint or "auth", alias=alias, request=request, status=403, mark_used=False)
        raise HTTPException(status_code=403, detail="This API has been deactivated.")
    credential = _consumer_credential(request, api_key)
    if not credential or not secrets.compare_digest(credential, row["api_key"]):
        _log_request(row["id"], endpoint or "auth", alias=alias, request=request, status=401, mark_used=False)
        raise HTTPException(status_code=401, detail="Invalid API key.")
    return row


@data_api_router.get("/{api_id}/tables")
def exec_list_tables(api_id: str, request: Request, api_key: Optional[str] = Query(None, deprecated=True)):
    """Consumer endpoint: list the table aliases exposed by a Data API Export.
    Prefer ``Authorization: Bearer <api-key>``. The legacy ``api_key`` query
    parameter remains temporarily supported for zero-downtime migration. Works
    with either the raw uuid or the friendly slug. 401 on a wrong/missing key,
    403 if the API has been deactivated."""
    with _db() as conn:
        row = _authorize_key(conn, api_id, api_key, request=request, endpoint="tables")
        tables = [_table_row(t) for t in _get_tables(conn, row["id"])]
    _log_request(row["id"], "tables", request=request, row_count=len(tables))
    return {"api_id": api_id, "tables": tables}


@data_api_router.get("/{api_id}/{alias}/columns")
def exec_get_columns(api_id: str, alias: str, request: Request, api_key: Optional[str] = Query(None, deprecated=True)):
    """Consumer endpoint: column names/types for one exposed table alias.
    Authentication behavior is the same as exec_list_tables."""
    with _db() as conn:
        row = _authorize_key(conn, api_id, api_key, request=request, endpoint="columns", alias=alias)
        t = conn.execute(
            "SELECT * FROM data_api_tables WHERE api_id = ? AND alias = ?", (row["id"], alias)
        ).fetchone()
    if not t:
        _log_request(row["id"], "columns", alias=alias, request=request, status=404, mark_used=False)
        raise HTTPException(status_code=404, detail=f"No table with alias '{alias}' on this API.")
    cfg = _source_cfg(t["source_system"], t["env"])
    try:
        with create_connector(t["source_system"], cfg) as conn2:
            columns = conn2.get_columns(t["database_name"], t["schema_name"], t["table_name"])
    except ConnectorError as e:
        _log_request(row["id"], "columns", alias=alias, request=request, status=400, mark_used=False)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error reading columns for data-api {api_id}/{alias}: {e}")
        _log_request(row["id"], "columns", alias=alias, request=request, status=502, mark_used=False)
        raise HTTPException(status_code=502, detail=f"Could not read columns: {e}")
    _log_request(row["id"], "columns", alias=alias, request=request, row_count=len(columns))
    return {"alias": alias, "columns": columns}


@data_api_router.get("/{api_id}/{alias}/count")
def exec_get_count(api_id: str, alias: str, request: Request, api_key: Optional[str] = Query(None, deprecated=True), search: str = Query(""), column: str = Query("")):
    """Consumer endpoint: total row count for one exposed table alias, optionally
    filtered by a search term across all columns, or a single column if `column`
    is given (much faster on wide/huge tables — see exec_get_rows)."""
    started = time.monotonic()
    with _db() as conn:
        row = _authorize_key(conn, api_id, api_key, request=request, endpoint="count", alias=alias)
        t = conn.execute(
            "SELECT * FROM data_api_tables WHERE api_id = ? AND alias = ?", (row["id"], alias)
        ).fetchone()
    if not t:
        _log_request(row["id"], "count", alias=alias, request=request, status=404, mark_used=False)
        raise HTTPException(status_code=404, detail=f"No table with alias '{alias}' on this API.")
    cfg = _source_cfg(t["source_system"], t["env"])
    search = (search or "").strip()
    column = (column or "").strip()
    try:
        with create_connector(t["source_system"], cfg) as conn2:
            if search:
                all_columns = [c["name"] for c in conn2.get_columns(t["database_name"], t["schema_name"], t["table_name"])]
                search_columns = _resolve_search_columns(column, all_columns)
                total_rows = conn2.search_row_count(t["database_name"], t["schema_name"], t["table_name"], search_columns, search)
            else:
                total_rows = conn2.get_row_count(t["database_name"], t["schema_name"], t["table_name"])
    except ConnectorError as e:
        _log_request(row["id"], "count", alias=alias, request=request, status=400, mark_used=False)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error counting rows for data-api {api_id}/{alias}: {e}")
        _log_request(row["id"], "count", alias=alias, request=request, status=502, mark_used=False)
        raise HTTPException(status_code=502, detail=f"Could not count rows: {e}")
    _log_request(
        row["id"], "count", alias=alias, request=request,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return {"alias": alias, "total_rows": total_rows, "max_offset": max(total_rows - 1, 0), "row_limit": t["row_limit"]}


@data_api_router.get("/{api_id}/{alias}/rows")
def exec_get_rows(
    api_id: str,
    alias: str,
    request: Request,
    api_key: Optional[str] = Query(None, deprecated=True),
    limit: int = Query(DEFAULT_ROW_LIMIT, ge=1, le=MAX_ROW_LIMIT),
    offset: int = Query(0, ge=0),
    search: str = Query(""),
    column: str = Query(""),
    include_total: bool = Query(False),
):
    """Consumer endpoint: paginated rows for one exposed table alias, with
    optional search. By default search checks every column (slow on huge/wide
    tables since every column gets cast and scanned); pass `column` to search
    just that one column instead — no casting the rest, no scanning big
    text/blob columns you don't care about, much faster. Page size is capped
    by the table's configured row_limit (and the hard MAX_ROW_LIMIT); use
    offset/next_offset to page through the rest — there is no cap on the
    table as a whole. Exact totals are opt-in via include_total=true because
    COUNT(*) can be expensive on very large sources. Prefer Bearer authentication; query-string keys are
    accepted only for compatibility during migration."""
    started = time.monotonic()
    with _db() as conn:
        row = _authorize_key(conn, api_id, api_key, request=request, endpoint="rows", alias=alias)
        t = conn.execute(
            "SELECT * FROM data_api_tables WHERE api_id = ? AND alias = ?", (row["id"], alias)
        ).fetchone()
        if not t:
            _log_request(row["id"], "rows", alias=alias, request=request, status=404, mark_used=False)
            raise HTTPException(status_code=404, detail=f"No table with alias '{alias}' on this API.")
        # This cap is per page, not per table — call again with a higher
        # `offset` to walk the rest. There's no limit on the table as a whole.
        capped_limit = min(int(limit), t["row_limit"], MAX_ROW_LIMIT)
        offset = max(int(offset), 0)
        search = (search or "").strip()
        column = (column or "").strip()

    cfg = _source_cfg(t["source_system"], t["env"])
    try:
        with create_connector(t["source_system"], cfg) as conn2:
            columns = [c["name"] for c in conn2.get_columns(t["database_name"], t["schema_name"], t["table_name"])]
            search_columns = _resolve_search_columns(column, columns)
            # Pagination needs a stable order or the same offset can return
            # different rows between calls. Default (whole-table) search
            # orders by every column since there's no known primary key.
            # Column-restricted search orders by just that column instead —
            # ordering by every column, including a big text/blob one, would
            # undo the whole point of narrowing the search (still a full
            # sort over the heaviest column on every call).
            if search:
                rows = conn2.search_rows(
                    t["database_name"], t["schema_name"], t["table_name"],
                    columns, search, capped_limit,
                    order_by=(search_columns if column else columns), offset=offset,
                    search_columns=search_columns,
                )
            else:
                rows = conn2.fetch_rows(
                    t["database_name"], t["schema_name"], t["table_name"],
                    columns, capped_limit, order_by=columns, offset=offset,
                )
            # Only counted on the first page — a full COUNT(*) on every page
            # of a huge table would be wasteful, and the caller only needs
            # it once to know how many pages to expect.
            total_rows = None
            if offset == 0 and include_total:
                try:
                    if search:
                        total_rows = conn2.search_row_count(t["database_name"], t["schema_name"], t["table_name"], search_columns, search)
                    else:
                        total_rows = conn2.get_row_count(t["database_name"], t["schema_name"], t["table_name"])
                except Exception:
                    total_rows = None  # non-fatal — has_more still works without it
    except ConnectorError as e:
        _log_request(row["id"], "rows", alias=alias, request=request, status=400, mark_used=False)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error reading rows for data-api {api_id}/{alias}: {e}")
        _log_request(row["id"], "rows", alias=alias, request=request, status=502, mark_used=False)
        raise HTTPException(status_code=502, detail=f"Could not read rows: {e}")

    _log_request(
        row["id"], "rows", alias=alias, request=request, row_count=len(rows),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    has_more = len(rows) == capped_limit
    return {
        "alias": alias,
        "row_count": len(rows),
        "total_rows": total_rows,
        "max_offset": (max(total_rows - 1, 0) if total_rows is not None else None),
        "offset": offset,
        "limit": capped_limit,
        "has_more": has_more,
        "next_offset": (offset + capped_limit) if has_more else None,
        "search": search or None,
        "column": column or None,
        "rows": rows,
    }