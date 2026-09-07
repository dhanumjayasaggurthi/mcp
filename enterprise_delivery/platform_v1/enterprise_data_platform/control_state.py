from __future__ import annotations

import threading
from typing import Dict, Generic, List, TypeVar

from .control_models import AgentRegistration, ClientRegistration, GuardrailRule, IndexDeployment

T = TypeVar("T")


class ResourceNotFound(KeyError):
    pass


class _Registry(Generic[T]):
    def __init__(self) -> None:
        self._items: Dict[str, T] = {}
        self._lock = threading.RLock()

    def put(self, item: T) -> T:
        item_id = getattr(item, "id")
        with self._lock:
            self._items[item_id] = item.model_copy(deep=True)
            return item.model_copy(deep=True)

    def get(self, item_id: str) -> T:
        with self._lock:
            if item_id not in self._items:
                raise ResourceNotFound(item_id)
            return self._items[item_id].model_copy(deep=True)

    def list(self) -> List[T]:
        with self._lock:
            return [self._items[k].model_copy(deep=True) for k in sorted(self._items)]

    def delete(self, item_id: str) -> None:
        with self._lock:
            if item_id not in self._items:
                raise ResourceNotFound(item_id)
            del self._items[item_id]


class ControlState:
    """Reference control-state adapter.

    Production deployments should back these registries with the shared control
    database and publish changes through an event bus/cache invalidation layer.
    """

    def __init__(self) -> None:
        self.clients: _Registry[ClientRegistration] = _Registry()
        self.agents: _Registry[AgentRegistration] = _Registry()
        self.guardrails: _Registry[GuardrailRule] = _Registry()
        self.indexes: _Registry[IndexDeployment] = _Registry()
