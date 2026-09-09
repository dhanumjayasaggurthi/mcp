"""Request IDs, bounded input/deadlines and low-cardinality OTel instruments."""
import asyncio
import re
import time
from .context import ExecutionContext, current_actor, current_context


class Metrics:
    def __init__(self):
        from opentelemetry import metrics
        meter = metrics.get_meter('enterprise_data_platform')
        self.requests = meter.create_counter('edp.requests')
        self.latency = meter.create_histogram('edp.request.duration', unit='s')
        self.rows = meter.create_counter('edp.rows.returned')
        self.bytes = meter.create_counter('edp.bytes.returned', unit='By')
        self.active = meter.create_up_down_counter('edp.requests.active')

    def record(self, operation, seconds, outcome='ok', rows=0, byte_count=0):
        labels = {'operation': operation, 'outcome': outcome}
        self.requests.add(1, labels)
        self.latency.record(seconds, labels)
        self.rows.add(rows, {'operation': operation})
        self.bytes.add(byte_count, {'operation': operation})


class RequestBoundary:
    def __init__(self, app, max_seconds=30, max_body_bytes=1_000_000):
        self.app, self.max_seconds, self.max_body_bytes = app, max_seconds, max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse
        headers = dict(scope['headers'])
        try:
            length = int(headers.get(b'content-length', b'0') or 0)
        except ValueError:
            return await JSONResponse({'detail': 'invalid content length', 'code': 'bad_request'}, 400)(scope, receive, send)
        if length > self.max_body_bytes:
            return await JSONResponse({'detail': 'request body exceeds byte budget', 'code': 'body_too_large'}, 413)(scope, receive, send)
        context = ExecutionContext()
        from opentelemetry import trace
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            context.trace_id = format(span_context.trace_id, '032x')
        supplied = headers.get(b'x-request-id', b'').decode('ascii', 'ignore')
        if not span_context.is_valid and re.fullmatch(r'[a-zA-Z0-9-]{1,64}', supplied):
            context.trace_id = supplied
        try:
            seconds = min(self.max_seconds, max(.1, int(headers.get(b'x-request-timeout-ms', b'30000')) / 1000))
        except ValueError:
            return await JSONResponse({'detail': 'invalid request timeout'}, 400)(scope, receive, send)
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
