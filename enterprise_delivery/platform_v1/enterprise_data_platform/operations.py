from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Protocol


class OperationsProvider(Protocol):
    """Bounded operational read model for dashboards at any data cardinality."""

    def snapshot(self) -> Dict[str, Any]: ...


class InMemoryOperationsProvider:
    """Thread-safe reference provider; production must supply durable telemetry."""

    def __init__(self, snapshot: Dict[str, Any] | None = None) -> None:
        self._lock = RLock()
        self._snapshot = deepcopy(snapshot or {})

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            value = deepcopy(self._snapshot)
        value["generated_at"] = datetime.now(timezone.utc).isoformat()
        return value


def reference_operations_snapshot() -> Dict[str, Any]:
    return {
        "environment": "prod", "environments": ["dev", "qa", "prod"], "system_status": "operational",
        "metrics": {
            "active_data_products": {"value": 342, "change": 12}, "healthy_indexes": {"value": "28.4M", "change": 0.6},
            "policy_violations": {"value": 12, "change": 20}, "active_consumers": {"value": 184, "change": 8},
            "p95_latency": {"value": 412, "unit": "ms", "change": -18}, "error_rate": {"value": 0.08, "unit": "%", "change": -35},
        },
        "deployment": {"current": "v2.4.1", "candidate": "v2.5.0-rc1", "traffic": 10, "stage": "canary"},
        "policy": {"allowed": 184, "total": 196, "masked_fields": 128, "masked_products": 42, "row_filters": 56, "quotas_near_limit": 4},
        "services": [
            {"name": "Structured (Metadata)", "status": "healthy", "qps": "12.4K", "p95_ms": 220},
            {"name": "Keyword Search", "status": "healthy", "qps": "18.7K", "p95_ms": 310},
            {"name": "Vector Search", "status": "healthy", "qps": "15.2K", "p95_ms": 480},
            {"name": "Hybrid Search", "status": "healthy", "qps": "9.1K", "p95_ms": 520},
            {"name": "RAG (Orchestration)", "status": "healthy", "qps": "4.8K", "p95_ms": 620},
        ],
        "mcp": {"tools": 8, "resources": 12, "pending": 3, "denied": 0},
        "consumers": [
            {"name": "RegAssist", "requests": "1.2B", "retrieved": "18.4M chunks", "change": 22},
            {"name": "Agent Services", "requests": "842M", "retrieved": "12.1M chunks", "change": 14},
            {"name": "Analytics", "requests": "316M", "retrieved": "5.8M chunks", "change": 9},
            {"name": "External Partner", "requests": "97M", "retrieved": "1.6M chunks", "change": 6},
        ],
        "alerts": [
            {"time": "10:18 AM", "severity": "high", "type": "Blocked Query", "message": "PII pattern detected in query from external partner", "status": "blocked"},
            {"time": "09:42 AM", "severity": "medium", "type": "Unusual Access", "message": "Spike in vector queries from new IP range", "status": "investigating"},
            {"time": "08:17 AM", "severity": "medium", "type": "Model Drift", "message": "Embedding distribution shift detected (2.7σ)", "status": "open"},
            {"time": "06:03 AM", "severity": "low", "type": "Threshold Breach", "message": "QPS exceeded 80% of quota for Analytics", "status": "resolved"},
        ],
        "audit_events": [
            {"time": "10:22 AM", "actor": "j.smith@health.org", "action": "Updated policy", "resource": "Row filter: rdh_patients", "environment": "PROD"},
            {"time": "10:15 AM", "actor": "regassist-service", "action": "Accessed data", "resource": "Vector index (1.2M chunks)", "environment": "PROD"},
            {"time": "09:56 AM", "actor": "m.chen@health.org", "action": "Approved MCP tool", "resource": "retrieve_guidelines", "environment": "PROD"},
            {"time": "09:14 AM", "actor": "system", "action": "Deployed version", "resource": "v2.5.0-rc1 (canary 10%)", "environment": "PROD"},
        ],
    }
