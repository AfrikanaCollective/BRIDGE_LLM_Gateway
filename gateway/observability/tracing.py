"""OpenTelemetry tracing setup — deliberately scoped to 2 spans per request.

See ARCHITECTURE.md §8.2 and PRD.md §10c: instrumenting every internal
function (rate limiter, budget tracker, DB writes...) was reviewed and cut
for a gateway that's one hop deep — Prometheus carries the metrics load
instead. Only two spans exist per request:

  1. "gateway.request"       — the whole handle_request() call
  2. "gateway.provider_call" — one child span per backend attempt (so a
                                failover shows up as sibling spans)

Do not add more spans without checking ARCHITECTURE.md §12(c) first.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from gateway.config import settings

_tracer: trace.Tracer | None = None


def configure_tracing() -> trace.Tracer:
    global _tracer
    if _tracer is not None:
        return _tracer

    provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(settings.otel_service_name)
    return _tracer


def get_tracer() -> trace.Tracer:
    return _tracer or configure_tracing()
