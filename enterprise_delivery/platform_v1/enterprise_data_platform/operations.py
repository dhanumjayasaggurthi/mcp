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


