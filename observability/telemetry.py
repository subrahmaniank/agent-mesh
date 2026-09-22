"""OpenTelemetry wiring shared by the gateway and the worker planes.

The platform declared opentelemetry-api / -sdk / -exporter-otlp in three
requirements files and imported them nowhere, so no span was ever produced and
the collector's Zero Data Retention transform had nothing to scrub.

Every function degrades to a no-op when the SDK is absent or no endpoint is
configured, so neither the gateway nor a worker fails to start because
telemetry is unavailable.
"""

import hashlib
import os
from contextlib import contextmanager
from typing import Dict, Optional

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

_tracer = None
_propagator = None
_enabled = False


def init(service_name: str) -> None:
    """Initialise a tracer provider exporting OTLP/gRPC. Safe to call twice."""
    global _tracer, _propagator, _enabled
    if _enabled or not OTLP_ENDPOINT:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.propagate import get_global_textmap
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
        trace.set_tracer_provider(provider)

        _tracer = trace.get_tracer(service_name)
        _propagator = get_global_textmap()
        _enabled = True
    except Exception:
        # Telemetry must never take the service down.
        _enabled = False


def payload_digest(payload: str) -> str:
    """SHA-256 of the raw payload.

    Lets an auditor prove which bytes a decision was made over without
    retaining the bytes themselves -- the "execution hashes" the observability
    docs promised but nothing computed.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@contextmanager
def span(name: str, attributes: Optional[Dict[str, object]] = None, carrier: Optional[Dict[str, str]] = None):
    """Start a span, continuing an upstream W3C trace when `carrier` has one."""
    if not _enabled or _tracer is None:
        yield None
        return

    from opentelemetry import trace

    context = None
    if carrier and _propagator is not None:
        try:
            context = _propagator.extract(carrier)
        except Exception:
            context = None

    with _tracer.start_as_current_span(name, context=context) as current:
        for key, value in (attributes or {}).items():
            try:
                current.set_attribute(key, value)
            except Exception:
                pass
        try:
            yield current
        except Exception as exc:
            current.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def inject(headers: Dict[str, str]) -> Dict[str, str]:
    """Add W3C traceparent to outbound headers so the trace crosses the hop."""
    if _enabled and _propagator is not None:
        try:
            _propagator.inject(headers)
        except Exception:
            pass
    return headers
