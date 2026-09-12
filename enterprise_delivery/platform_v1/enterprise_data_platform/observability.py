"""Request IDs, bounded input/deadlines and low-cardinality OTel instruments."""
import asyncio
import re
import time
from .context import ExecutionContext, current_actor, current_context


class Metrics:
    def __init__(self):
        from collections import deque
        from threading import RLock
        self._samples = deque(maxlen=10000)
        self._lock = RLock()
        self._started = time.monotonic()
        self._last_eviction = None
        from opentelemetry import metrics
        meter = metrics.get_meter('enterprise_data_platform')
        self.requests = meter.create_counter('edp.requests')
        self.latency = meter.create_histogram('edp.request.duration', unit='s')
        self.rows = meter.create_counter('edp.rows.returned')
        self.bytes = meter.create_counter('edp.bytes.returned', unit='By')
        self.active = meter.create_up_down_counter('edp.requests.active')

    def record(self, operation, seconds, outcome='ok', rows=0, byte_count=0):
        with self._lock:
            if len(self._samples) == self._samples.maxlen:
                self._last_eviction = self._samples[0][0]
            self._samples.append((time.monotonic(), operation, max(0, seconds), outcome, rows, byte_count))
        labels = {'operation': operation, 'outcome': outcome}
        self.requests.add(1, labels)
        self.latency.record(seconds, labels)
        self.rows.add(rows, {'operation': operation})
        self.bytes.add(byte_count, {'operation': operation})

    def snapshot(self):
        import math
        import os
        from datetime import datetime, timezone
        now = time.monotonic()
        with self._lock:
            samples = [s for s in self._samples if s[0] >= now - 300]
            truncated = self._last_eviction is not None and self._last_eviction >= now - 300
        def summary(rows):
            latencies = sorted(s[2] * 1000 for s in rows)
            return {"requests": len(rows), "p95_ms": round(latencies[math.ceil(len(rows) * .95) - 1], 2) if rows else None,
                    "error_rate": round(sum(s[3] not in {"ok", "denied", "overloaded"} for s in rows) * 100 / len(rows), 2) if rows else None,
                    "denied": sum(s[3] == "denied" for s in rows),
                    "overloaded": sum(s[3] == "overloaded" for s in rows),
                    "returned_rows": sum(s[4] for s in rows)}
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "scope": "replica",
                "instance": os.getenv("HOSTNAME", "current-process"), "window_seconds": 300,
                "sample_limit": self._samples.maxlen, "truncated": truncated,
                "covered_seconds": round(min(300, now - self._started, now - samples[0][0] if truncated and samples else 300), 2),
                "exporter_configured": bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")),
                "totals": summary(samples),
                "operations": [{"operation": operation, **summary([s for s in samples if s[1] == operation])}
                               for operation in sorted({s[1] for s in samples})]}



class RequestBoundary:
    def __init__(self, app, max_seconds=30, max_body_bytes=1_000_000):
        self.app, self.max_seconds, self.max_body_bytes = app, max_seconds, max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse
        headers = dict(scope['headers'])
        context = ExecutionContext()
        from opentelemetry import trace
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            context.trace_id = format(span_context.trace_id, '032x')
        supplied = headers.get(b'x-request-id', b'').decode('ascii', 'ignore')
        if not span_context.is_valid and re.fullmatch(r'[a-zA-Z0-9-]{1,64}', supplied):
            context.trace_id = supplied
        async def reject(detail, status):
            return await JSONResponse({'detail': detail, 'code': str(status), 'trace_id': context.trace_id}, status,
                headers={'x-request-id': context.trace_id, 'x-trace-id': context.trace_id})(scope, receive, send)
        try:
            length = int(headers.get(b'content-length', b'0') or 0)
            if length < 0:
                raise ValueError('negative content length')
        except ValueError:
            return await reject('invalid content length', 400)
        if length > self.max_body_bytes:
            return await reject('request body exceeds byte budget', 413)
        try:
            seconds = min(self.max_seconds, max(.1, int(headers.get(b'x-request-timeout-ms', b'30000')) / 1000))
        except ValueError:
            return await reject('invalid request timeout', 400)
        context.deadline = time.monotonic() + seconds
        context.idempotency_key = headers.get(b'idempotency-key', b'').decode('ascii', 'ignore') or None
        token = current_context.set(context)
        actor_token = current_actor.set(None)
        started, received = False, 0
        async def receive_bounded():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.disconnect':
                context.cancelled.set()
            received += len(message.get('body', b''))
            if received > self.max_body_bytes:
                from starlette.exceptions import HTTPException
                raise HTTPException(413, 'request body exceeds byte budget')
            return message
        async def send_traced(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message['headers'] = [*message.get('headers', []),
                    (b'x-request-id', context.trace_id.encode()), (b'x-trace-id', context.trace_id.encode())]
            await send(message)
        try:
            await asyncio.wait_for(self.app(scope, receive_bounded, send_traced), timeout=seconds)
        except (asyncio.TimeoutError, TimeoutError):
            context.cancelled.set()
            if not started:
                await JSONResponse({'detail': 'request deadline exceeded', 'code': 'deadline_exceeded',
                    'trace_id': context.trace_id}, 504)(scope, receive, send_traced)
        finally:
            current_actor.reset(actor_token)
            current_context.reset(token)


class SafeTelemetry:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        tracer = trace.get_tracer('enterprise_data_platform')
        carrier = {k.decode('ascii'): v.decode('ascii', 'ignore')[:256] for k,v in scope['headers'] if k == b'traceparent'}
        parent = TraceContextTextMapPropagator().extract(carrier)
        with tracer.start_as_current_span('http.request', context=parent, record_exception=False, set_status_on_exception=False) as span:
            span.set_attribute('http.request.method', scope['method'])
            async def send_safe(message):
                if message['type'] == 'http.response.start':
                    span.set_attribute('http.response.status_code', message['status'])
                    route = scope.get('route')
                    if route and hasattr(route, 'path'):
                        span.set_attribute('http.route', route.path)
                await send(message)
            started = time.monotonic()
            try:
                await self.app(scope, receive, send_safe)
            finally:
                import json
                span_context = span.get_span_context()
                print(json.dumps({'event': 'http.complete', 'trace_id': format(span_context.trace_id, '032x'),
                    'duration_seconds': round(time.monotonic()-started, 6), 'method': scope['method']}), flush=True)


class TelemetryProxy:
    def __init__(self, target, name):
        self.target, self.name = target, name

    def __getattr__(self, name):
        value = getattr(self.target, name)
        if name not in {'query', 'aggregate', 'search', 'get_chunks', 'embed', 'embed_batch', 'score', 'submit_governed'}:
            return value
        def invoke(*args, **kwargs):
            from opentelemetry import trace
            with trace.get_tracer('enterprise_data_platform').start_as_current_span(
                self.name + '.' + name, record_exception=False, set_status_on_exception=False):
                return value(*args, **kwargs)
        return invoke


def configure_telemetry():
    import os
    if not os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT'):
        return
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    resource = Resource.create({'service.name': os.getenv('OTEL_SERVICE_NAME','enterprise-data-platform')})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), max_queue_size=2048))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15000)]))


def instrument(app):
    app.add_middleware(SafeTelemetry)
