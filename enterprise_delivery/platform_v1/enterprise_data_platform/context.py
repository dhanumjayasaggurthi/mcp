"""Request-local context: no cross-request correctness state."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event
import time
import uuid


@dataclass
class ExecutionContext:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    deadline: float = field(default_factory=lambda: time.monotonic() + 30)
    cancelled: Event = field(default_factory=Event)
    workload: str = 'interactive'
    idempotency_key: str | None = None

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise TimeoutError('request deadline exceeded or cancelled')
        return remaining


current_context: ContextVar[ExecutionContext | None] = ContextVar('edp_context', default=None)
current_actor: ContextVar[dict | None] = ContextVar('edp_actor', default=None)


@contextmanager
def execution_context(context=None):
    value = context or current_context.get() or ExecutionContext()
    token = current_context.set(value)
    try:
        value.remaining()
        yield value
    finally:
        current_context.reset(token)


def trace_id():
    context = current_context.get()
    return context.trace_id if context else uuid.uuid4().hex
