"""Bounded per-adapter circuit state is advisory, never authoritative policy."""
import random
import threading
import time
from .context import current_context


class BackendUnavailable(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, threshold=5, recovery_seconds=10):
        self.threshold, self.recovery = threshold, recovery_seconds
        self._failures = 0
        self._open_until = 0.0
        self._probing = False
        self._lock = threading.Lock()

    def call(self, function, *, retryable=(ConnectionError, TimeoutError), attempts=3):
        with self._lock:
            if self._open_until and (time.monotonic() < self._open_until or self._probing):
                raise BackendUnavailable('backend circuit is open')
            if self._open_until:
                self._probing = True
        try:
            for attempt in range(attempts):
                context = current_context.get()
                if context:
                    context.remaining()
                try:
                    value = function()
                    with self._lock:
                        self._failures = 0
                        self._open_until = 0
                    return value
                except retryable:
                    if attempt == attempts - 1:
                        raise
                    delay = random.uniform(0, min(1, .05 * 2**attempt))
                    if context and context.remaining() <= delay:
                        raise TimeoutError('retry would exceed deadline')
                    if context:
                        context.cancelled.wait(delay)
                    else:
                        time.sleep(delay)
        except retryable:
            with self._lock:
                self._failures += 1
                if self._failures >= self.threshold:
                    self._open_until = time.monotonic() + self.recovery
            raise
        finally:
            with self._lock:
                self._probing = False
