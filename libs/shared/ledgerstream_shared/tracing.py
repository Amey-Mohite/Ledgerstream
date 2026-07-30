"""OpenTelemetry tracer setup.

Every service calls `configure_tracing(service_name)` once at startup. Spans are
exported over OTLP/gRPC to the collector, which fans them out to Jaeger. Because
the correlation id is also attached as a span attribute, a single request can be
followed BOTH in the logs (by correlation_id) and in the trace UI (by trace_id).

Degrade-gracefully: if the OTLP endpoint is unset we fall back to a no-op-ish
provider so local unit tests and one-off scripts don't require a running
collector.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from ledgerstream_shared.config import get_env

_configured = False


def configure_tracing(service_name: str) -> trace.Tracer:
    """Install a global TracerProvider for this service and return a tracer.

    Idempotent — subsequent calls return a tracer without re-installing the
    provider (OTel warns if a provider is set twice).
    """
    global _configured

    if not _configured:
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        endpoint = get_env("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        else:
            # No collector configured (e.g. unit tests): print spans instead.
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _configured = True

    return trace.get_tracer(service_name)
