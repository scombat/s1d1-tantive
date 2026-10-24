"""JSON logs on stdout, Prometheus series, OpenTelemetry wiring (spec § 5). Prompt text is never logged by default."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from opentelemetry.sdk.resources import SERVICE_VERSION, Resource

from tantive import __version__
from tantive.model import Outputs

SERVICE_NAME = "s1d1-tantive"
RESOURCE = Resource.create({"service.name": SERVICE_NAME, SERVICE_VERSION: __version__})
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 2.0)
TOKEN_BUCKETS = (16, 32, 64, 128, 256, 512, 768, 1024)


def otlp_url(endpoint: str, signal: str) -> str | None:
    """The OTLP/HTTP URL for a signal: the standard per-signal variable when the operator set it (the exporter reads
    it when given None), else the endpoint from the settings plus the signal's path."""
    if os.environ.get(f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT"):
        return None
    return f"{endpoint.rstrip('/')}/v1/{signal}"


class Metrics:
    """The series of spec § 5, defined once as OpenTelemetry instruments: read by the Prometheus exporter for
    `/metrics` and, when an OTLP endpoint is set, pushed by the OTLP metric exporter. One per process."""

    def __init__(self, otlp_endpoint: str | None) -> None:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from opentelemetry.sdk.metrics import MeterProvider

        readers = [PrometheusMetricReader()]
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            exporter = OTLPMetricExporter(endpoint=otlp_url(otlp_endpoint, "metrics"))
            readers.append(PeriodicExportingMetricReader(exporter))
        self.otlp = bool(otlp_endpoint)
        self.provider = MeterProvider(resource=RESOURCE, metric_readers=readers)
        meter = self.provider.get_meter("tantive", __version__)
        self._latency = meter.create_histogram(
            "tantive_request_latency",
            unit="s",
            description="Wall-clock time of a decision request, queue included",
            explicit_bucket_boundaries_advisory=LATENCY_BUCKETS,
        )
        self._decisions = meter.create_counter(
            "tantive_decisions", description="Decisions by tier (native names; `none` without a tier)"
        )
        self._tokens = meter.create_histogram(
            "tantive_prompt_tokens",
            description="Tokens of the classified text",
            explicit_bucket_boundaries_advisory=TOKEN_BUCKETS,
        )
        self._in_flight = meter.create_up_down_counter(
            "tantive_in_flight_requests", description="Decision requests accepted and not yet answered"
        )
        self._overloaded = meter.create_counter(
            "tantive_overloaded", description="Requests rejected with 503 by the deadline scheduler"
        )
        self._capture_written = meter.create_counter(
            "tantive_capture_written", description="Decisions appended to the capture files"
        )
        self._capture_dropped = meter.create_counter(
            "tantive_capture_dropped", description="Decisions lost by the capture (full queue or write error)"
        )
        self._capture_sent = meter.create_counter(
            "tantive_capture_sent", description="Captured lines accepted by the community ingestion"
        )
        self._capture_send_failed = meter.create_counter(
            "tantive_capture_send_failed", description="Batches not delivered (network error or retryable status)"
        )
        self._capture_send_rejected = meter.create_counter(
            "tantive_capture_send_rejected",
            description="Captured lines given up: refused by the ingestion (400, 413), unreadable or oversized",
        )
        self._capture_send_pending = meter.create_gauge(
            "tantive_capture_send_pending_bytes",
            description="Bytes of capture files after the send cursor (updated after each read by the sender)",
        )

    def capture_written(self) -> None:
        self._capture_written.add(1)

    def capture_dropped(self) -> None:
        self._capture_dropped.add(1)

    def capture_sent(self, n: int) -> None:
        self._capture_sent.add(n)

    def capture_send_failed(self) -> None:
        self._capture_send_failed.add(1)

    def capture_send_rejected(self, n: int) -> None:
        self._capture_send_rejected.add(n)

    def capture_send_pending(self, n: int) -> None:
        self._capture_send_pending.set(n)

    def record_request(self, route: str, fmt: str, seconds: float, tier: str | None, tokens: int | None) -> None:
        self._latency.record(seconds, {"route": route, "format": fmt})
        self._decisions.add(1, {"tier": tier or "none"})
        if tokens is not None:
            self._tokens.record(tokens)

    def overloaded(self) -> None:
        self._overloaded.add(1)

    @contextmanager
    def in_flight(self) -> Iterator[None]:
        self._in_flight.add(1)
        try:
            yield
        finally:
            self._in_flight.add(-1)

    def shutdown(self) -> None:
        self.provider.shutdown()


_metrics: Metrics | None = None


def metrics(otlp_endpoint: str | None = None) -> Metrics:
    """The process-wide Metrics, created on first call (the Prometheus reader registers in the global registry, so
    there can be only one); the OTLP reader exists only when the first call names an endpoint."""
    global _metrics
    if _metrics is None:
        _metrics = Metrics(otlp_endpoint)
    elif bool(otlp_endpoint) != _metrics.otlp:
        log_event("metrics_already_configured", level=logging.WARNING, otlp=_metrics.otlp)
    return _metrics


_log = logging.getLogger("tantive")
_log_prompts = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        line.update(getattr(record, "fields", {}))
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at emit time (so a redirected or captured stdout is honoured)."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_logging(log_prompts: bool, level: int = logging.INFO) -> None:
    """One JSON line per event on stdout; idempotent (the handler is replaced)."""
    global _log_prompts
    _log_prompts = log_prompts
    handler = _StdoutHandler()
    handler.setFormatter(_JsonFormatter())
    _log.handlers[:] = [handler]
    _log.setLevel(level)
    _log.propagate = False


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    _log.log(level, event, extra={"fields": fields})


def log_decision(
    route: str,
    fmt: str,
    outputs: Outputs,
    tier: str | None,
    latency_ms: float,
    allowed: Sequence[str] | None,
    prompt: str | None,
) -> None:
    """The decision line; the prompt only when S1D1_LOG_PROMPTS=1."""
    fields: dict[str, Any] = {
        "route": route,
        "format": fmt,
        "tier": tier,
        "score": outputs.complexity_score,
        "task": outputs.task,
        "needs_tools": outputs.needs_tools_probability,
        "tokens": outputs.prompt_tokens,
        "truncated": outputs.truncated,
        "pieces": outputs.pieces,
        "latency_ms": round(latency_ms, 1),
        "allowed": list(allowed) if allowed is not None else None,
    }
    if _log_prompts and prompt is not None:
        fields["prompt"] = prompt
    log_event("decision", **fields)


class Tracing:
    """The process-wide tracer provider: OTLP/HTTP span export when an endpoint is set. One per process, because
    `trace.set_tracer_provider` is honoured once; later apps reuse it."""

    def __init__(self, otlp_endpoint: str | None) -> None:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        self.otlp = bool(otlp_endpoint)
        self.provider = TracerProvider(resource=RESOURCE)
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            exporter = OTLPSpanExporter(endpoint=otlp_url(otlp_endpoint, "traces"))
            self.provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(self.provider)

    def shutdown(self) -> None:
        self.provider.shutdown()


_tracing: Tracing | None = None


def tracing(otlp_endpoint: str | None = None) -> Tracing:
    """The process-wide Tracing, created on first call; a later call with another endpoint setting is logged."""
    global _tracing
    if _tracing is None:
        _tracing = Tracing(otlp_endpoint)
    elif bool(otlp_endpoint) != _tracing.otlp:
        log_event("tracing_already_configured", level=logging.WARNING, otlp=_tracing.otlp)
    return _tracing


def configure_otel(app: FastAPI, endpoint: str | None) -> bool:
    """Metrics and tracing singletons (their OTLP exporters only when the first call names an endpoint), plus the
    FastAPI instrumentation of this app when an endpoint is set.

    The exporters get `endpoint` (from the settings) plus the signal path, unless the operator set the standard
    per-signal OTEL_EXPORTER_OTLP_<SIGNAL>_ENDPOINT variable, which then wins."""
    metrics(endpoint)
    if not endpoint:
        return False
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tracing(endpoint).provider, excluded_urls="health,ready,metrics"
    )
    return True
