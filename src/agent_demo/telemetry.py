"""OpenTelemetry tracing, metrics and logs setup for agent-demo.

Traces, metrics and logs are exported over OTLP (gRPC by default) to an
OpenTelemetry Collector, which fans them out to Tempo (traces),
Prometheus (metrics) and Loki (logs) for display in Grafana.

Everything is a no-op when ``OTEL_ENABLED`` is false or the optional
OpenTelemetry packages are not installed.
"""

import logging
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
_logger_provider = None
_logging_setup = False


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def setup_telemetry(service_name: str | None = None) -> bool:
    """Configure OTLP trace, metric and log exporters.

    Returns ``True`` when telemetry is active, ``False`` when disabled.
    Console logging is always configured. Safe to call multiple times.
    """
    global _enabled
    if _enabled:
        return True

    _setup_console_logging()

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
        "agent.requests", unit="{request}", description="Agent 问答请求数"
    )
    _instruments["tokens"] = meter.create_counter(
        "agent.tokens", unit="{token}", description="LLM 消耗的 Token 数量"
    )
    _instruments["llm_duration"] = meter.create_histogram(
        "agent.llm.duration", unit="s", description="LLM 调用耗时"
    )
    _instruments["tool_calls"] = meter.create_counter(
        "agent.tool.calls", unit="{call}", description="Agent 工具调用次数"
    )

    _setup_otlp_logging(resource, endpoint, insecure)

    _enabled = True
    return True


def _setup_console_logging() -> None:
    """Attach a console handler to the root logger (independent of OTEL)."""
    global _logging_setup
    if _logging_setup:
        return
    _logging_setup = True

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(console)


def _setup_otlp_logging(resource: Resource, endpoint: str, insecure: bool) -> None:
    """Attach an OTLP log exporter to the root logger (feeds Loki)."""
    global _logger_provider

    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    _logger_provider = LoggerProvider(resource=resource)
    _logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=insecure))
    )
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.NOTSET, logger_provider=_logger_provider)
    )


def shutdown_telemetry() -> None:
    """Flush and shut down the trace, metric and log providers."""
    tracer_provider = trace.get_tracer_provider()
    if hasattr(tracer_provider, "shutdown"):
        tracer_provider.shutdown()
    meter_provider = metrics.get_meter_provider()
    if hasattr(meter_provider, "shutdown"):
        meter_provider.shutdown()
    global _logger_provider
    if _logger_provider is not None:
        _logger_provider.shutdown()
        _logger_provider = None


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


def current_trace_id() -> str:
    """Return the current trace id as hex, or an empty string when absent."""
    span = trace.get_current_span()
    context = span.get_span_context()
    if context is not None and context.trace_id:
        return format(context.trace_id, "032x")
    return ""
