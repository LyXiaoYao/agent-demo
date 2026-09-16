"""OpenTelemetry tracing and metrics setup for agent-demo.

Traces and metrics are exported over OTLP (gRPC by default) to an
OpenTelemetry Collector, which fans them out to Tempo (traces) and
Prometheus (metrics) for display in Grafana.

Everything is a no-op when ``OTEL_ENABLED`` is false or the optional
OpenTelemetry packages are not installed.
"""

import os

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_tracer = trace.get_tracer("agent_demo")
_instruments: dict = {}
_enabled = False


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def setup_telemetry(service_name: str | None = None) -> bool:
    """Configure OTLP trace and metric exporters.

    Returns ``True`` when telemetry is active, ``False`` when disabled.
    Safe to call multiple times.
    """
    global _enabled
    if _enabled:
        return True
    if not _env_bool("OTEL_ENABLED", True):
        return False

    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    insecure = endpoint.startswith("http://")

    resource = Resource.create(
        {
            "service.name": service_name
            or os.getenv("OTEL_SERVICE_NAME", "agent-demo"),
            "service.version": "0.1.0",
            "deployment.environment": os.getenv("DEPLOYMENT_ENV", "dev"),
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=insecure))
    )
    trace.set_tracer_provider(tracer_provider)

    interval = int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "10000"))
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=insecure),
        export_interval_millis=interval,
    )
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[reader])
    )

    meter = metrics.get_meter("agent_demo")
    _instruments["requests"] = meter.create_counter(
        "agent.requests", unit="{request}", description="Agent chat requests."
    )
    _instruments["tokens"] = meter.create_counter(
        "agent.tokens", unit="{token}", description="LLM tokens consumed."
    )
    _instruments["llm_duration"] = meter.create_histogram(
        "agent.llm.duration", unit="s", description="LLM call duration."
    )
    _instruments["tool_calls"] = meter.create_counter(
        "agent.tool.calls", unit="{call}", description="Agent tool invocations."
    )

    _enabled = True
    return True


def shutdown_telemetry() -> None:
    """Flush and shut down the trace and metric providers."""
    tracer_provider = trace.get_tracer_provider()
    if hasattr(tracer_provider, "shutdown"):
        tracer_provider.shutdown()
    meter_provider = metrics.get_meter_provider()
    if hasattr(meter_provider, "shutdown"):
        meter_provider.shutdown()


def start_span(name: str, attributes: dict | None = None, context=None):
    """Start a span, optionally parented to ``context``."""
    return _tracer.start_span(name, attributes=attributes, context=context)


def span_context(span):
    """Return a context carrying ``span`` as the parent."""
    return trace.set_span_in_context(span)


def record_request(status: str) -> None:
    counter = _instruments.get("requests")
    if counter is not None:
        counter.add(1, {"agent.status": status})


def record_token_usage(model: str, input_tokens, output_tokens) -> None:
    counter = _instruments.get("tokens")
    if counter is None:
        return
    if input_tokens:
        counter.add(
            input_tokens, {"token.type": "input", "gen_ai.request.model": model}
        )
    if output_tokens:
        counter.add(
            output_tokens, {"token.type": "output", "gen_ai.request.model": model}
        )


def record_llm_duration(model: str, seconds: float) -> None:
    histogram = _instruments.get("llm_duration")
    if histogram is not None:
        histogram.record(seconds, {"gen_ai.request.model": model})


def record_tool_call(tool_name: str, status: str = "success") -> None:
    counter = _instruments.get("tool_calls")
    if counter is not None:
        counter.add(1, {"gen_ai.tool.name": tool_name, "tool.status": status})
