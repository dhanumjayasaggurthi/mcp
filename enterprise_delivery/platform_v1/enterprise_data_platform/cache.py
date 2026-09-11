"""Bounded optional accelerators; authoritative decisions never depend on them."""
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
import threading
import time


class CacheClass(str, Enum):
    CATALOG = 'catalog'
    POLICY = 'policy'
    SCHEMA = 'schema'
    CAPABILITY = 'capability'
    PLAN = 'plan'
    RESULT = 'result'
    EMBEDDING = 'embedding'


@dataclass(frozen=True)
class CacheKey:
    category: CacheClass
    tenant: str
    security_scope: str
    revision: str
    key: str


class BoundedCache:
    def __init__(self, max_entries=256, ttl_seconds=60, max_bytes=8_000_000):
        if min(max_entries, ttl_seconds, max_bytes) <= 0:
            raise ValueError('cache bounds must be positive')
        self.max_entries, self.ttl, self.max_bytes = max_entries, ttl_seconds, max_bytes
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self.hits = self.misses = 0
        from opentelemetry import metrics
        self._access = metrics.get_meter('enterprise_data_platform').create_counter('edp.cache.access')

    def get(self, key):
        with self._lock:
            item = self._entries.get(key)
            if item and item[0] > time.monotonic():
                self.hits += 1
                self._access.add(1, {'category': key.category.value, 'outcome': 'hit'})
                self._entries.move_to_end(key)
                return item[1]
            if item:
                self._bytes -= self._entries.pop(key)[2]
            self.misses += 1
            self._access.add(1, {'category': key.category.value, 'outcome': 'miss'})
            return None

    def put(self, key, value, *, size_bytes):
        if size_bytes < 0 or size_bytes > self.max_bytes:
            return
        with self._lock:
            old = self._entries.pop(key, None)
            if old:
                self._bytes -= old[2]
            self._entries[key] = (time.monotonic() + self.ttl, value, size_bytes)
            self._bytes += size_bytes
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                self._bytes -= self._entries.popitem(last=False)[1][2]

    def invalidate(self, predicate=lambda _: True):
        with self._lock:
            for key in list(self._entries):
                if predicate(key):
                    self._bytes -= self._entries.pop(key)[2]
