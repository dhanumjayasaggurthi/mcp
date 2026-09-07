from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .models import DataProduct


class CatalogConflict(RuntimeError):
    pass


class CatalogNotFound(KeyError):
    pass


class CatalogStore:
    def get(self, dataset_id: str) -> DataProduct:
        raise NotImplementedError

    def list(self) -> List[DataProduct]:
        raise NotImplementedError

    def put(self, product: DataProduct, *, expected_version: Optional[str] = None) -> DataProduct:
        raise NotImplementedError

    def delete(self, dataset_id: str, *, expected_version: Optional[str] = None) -> None:
        raise NotImplementedError


class InMemoryCatalog(CatalogStore):
    """Thread-safe reference catalog.

    Production should bind the same interface to a highly available SQL control
    store. ``expected_version`` provides optimistic concurrency so two control
    hub operators cannot silently overwrite each other's changes.
    """

    def __init__(self) -> None:
        self._items: Dict[str, DataProduct] = {}
        self._lock = threading.RLock()

    def get(self, dataset_id: str) -> DataProduct:
        with self._lock:
            try:
                return self._items[dataset_id].model_copy(deep=True)
            except KeyError as exc:
                raise CatalogNotFound(dataset_id) from exc

    def list(self) -> List[DataProduct]:
        with self._lock:
            return [self._items[k].model_copy(deep=True) for k in sorted(self._items)]

    def put(self, product: DataProduct, *, expected_version: Optional[str] = None) -> DataProduct:
        product.validate_contract()
        with self._lock:
            existing = self._items.get(product.id)
            if expected_version is not None:
                if existing is None:
                    raise CatalogConflict("dataset does not exist for conditional update")
                if existing.version != expected_version:
                    raise CatalogConflict(
                        f"version conflict: expected {expected_version}, current {existing.version}"
                    )
            self._items[product.id] = product.model_copy(deep=True)
            return product.model_copy(deep=True)

    def delete(self, dataset_id: str, *, expected_version: Optional[str] = None) -> None:
        with self._lock:
            existing = self._items.get(dataset_id)
            if existing is None:
                raise CatalogNotFound(dataset_id)
            if expected_version is not None and existing.version != expected_version:
                raise CatalogConflict(
                    f"version conflict: expected {expected_version}, current {existing.version}"
                )
            del self._items[dataset_id]
