"""Authenticated console read models. No invented fleet or deployment health."""
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query
from sqlalchemy import and_, or_, select

from .durable import audits
from .mcp_facade import MCPFacade


def control_page(registry, name, after="", limit=100):
    """Bounded control-plane keyset paging, including reference stores."""
    if hasattr(registry, "store"):
        items = registry.list(after=after, limit=limit + 1)
    elif hasattr(registry, "registry"):
        items = registry.registry.list(after=after, limit=limit + 1)
    else:
        items = sorted((x for x in registry.list() if x.id > after), key=lambda x: x.id)[:limit + 1]
    page = items[:limit]
    return {name: [x.model_dump(mode="json") for x in page],
            "next_cursor": page[-1].id if len(items) > limit else None,
            "returned_items": len(page)}


def register_console(app, service, control_admin_check, runtime_mode):
    principal_dep, admin_dep = app.state.principal_dependency, app.state.admin_dependency

    @app.get("/v1/control/session")
    def session(principal=Depends(principal_dep)):
        admin = bool(control_admin_check(principal))
        environment = os.getenv("EDP_ENVIRONMENT", "dev" if runtime_mode == "reference" else
                                "prod" if runtime_mode == "production" else "unspecified")
        return {
            "subject": principal.subject, "client_id": principal.client_id,
            "tenant": principal.tenant, "groups": sorted(principal.groups),
            "scopes": sorted(principal.attributes.get("oauth_scope", "").split()),
            "is_admin": admin, "environment": environment,
            "runtime_mode": runtime_mode, "release": os.getenv("EDP_RELEASE"),
            "routes": sorted({r.path for r in app.routes if hasattr(r, "path")}) if admin else [],
        }

    @app.get("/v1/control/audit")
    def audit_feed(
        limit: int = Query(50, ge=1, le=100),
        before_time: str | None = Query(None, max_length=40),
        before_id: str | None = Query(None, max_length=36),
        action: str | None = Query(None, max_length=128),
        subject: str | None = Query(None, max_length=512),
        resource: str | None = Query(None, max_length=512),
        since: datetime | None = None,
        principal=Depends(admin_dep),
    ):
        store = getattr(service, "store", None)
        if store is None:
            raise HTTPException(503, "Durable audit storage is not configured in this runtime")
        if bool(before_time) != bool(before_id):
            raise ValueError("before_time and before_id must be provided together")
        if before_time:
            parsed = datetime.fromisoformat(before_time)
            if parsed.tzinfo is None:
                raise ValueError("before_time must include a timezone")
            before_time = parsed.astimezone(timezone.utc).isoformat()
        lower = since or (datetime.now(timezone.utc) - timedelta(days=7))
        if lower.tzinfo is None:
            raise ValueError("since must include a timezone")
        lower = lower.astimezone(timezone.utc).isoformat()
        query = select(audits.c.id, audits.c.created_at, audits.c.action,
                       audits.c.resource, audits.c.actor, audits.c.trace_id).where(audits.c.created_at >= lower)
        if before_time:
            query = query.where(or_(audits.c.created_at < before_time,
                and_(audits.c.created_at == before_time, audits.c.id < before_id)))
        if action:
            query = query.where(audits.c.action == action)
        if subject:
            query = query.where(audits.c.actor["subject"].as_string() == subject)
        if resource:
            query = query.where(audits.c.resource == resource)
        with store.engine.connect() as conn:
            rows = conn.execute(query.order_by(audits.c.created_at.desc(), audits.c.id.desc())
                                .limit(limit + 1)).mappings().all()
        events = [{"id": r["id"], "time": r["created_at"], "action": r["action"],
                   "resource": r["resource"], "actor": r["actor"].get("subject", "unknown"),
                   "trace_id": r["trace_id"]} for r in rows[:limit]]
        return {"events": events, "since": lower,
                "next_cursor": {"before_time": events[-1]["time"], "before_id": events[-1]["id"]}
                if len(rows) > limit else None}

    @app.get("/v1/control/mcp/tools")
    def mcp_tools(principal=Depends(admin_dep)):
        return {"tools": MCPFacade.tool_definitions(),
                "surface": "governed_facade",
                "description": "Registered tool definitions. A tool definition does not establish a deployed MCP transport."}

    @app.get("/v1/control/telemetry")
    def telemetry(principal=Depends(admin_dep)):
        metrics = getattr(service, "metrics", None)
        if not metrics or not hasattr(metrics, "snapshot"):
            raise HTTPException(503, "Request telemetry is not configured in this runtime")
        return metrics.snapshot()
