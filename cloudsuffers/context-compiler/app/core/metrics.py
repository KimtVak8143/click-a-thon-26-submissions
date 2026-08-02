"""OpenTelemetry integration for Context Compiler.

This module configures OpenTelemetry for automatic tracing and metrics collection.
Metrics are exported to ClickHouse via OTEL Collector.
"""

import os
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_NAMESPACE,
    SERVICE_VERSION,
    DEPLOYMENT_ENVIRONMENT,
    Resource,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.logging import get_logger

logger = get_logger(__name__)


def _is_otel_enabled() -> bool:
    """Check if OpenTelemetry is enabled via environment variable."""
    return os.getenv("OTEL_ENABLED", "false").lower() == "true"


def _get_resource() -> Resource:
    """Create OTEL resource with service attributes."""
    return Resource(
        attributes={
            SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "context-compiler"),
            SERVICE_VERSION: "0.1.0",
            SERVICE_NAMESPACE: os.getenv("OTEL_RESOURCE_ATTRIBUTES", "").split(
                "service.namespace="
            )[-1].split(",")[0]
            or "cloudsuffers",
            DEPLOYMENT_ENVIRONMENT: os.getenv("CONTEXT_COMPILER_APP_ENV", "development"),
        }
    )


def configure_otel() -> tuple[TracerProvider | None, MeterProvider | None]:
    """
    Configure OpenTelemetry for traces and metrics.

    Returns:
        Tuple of (TracerProvider, MeterProvider) if OTEL is enabled, else (None, None)
    """
    if not _is_otel_enabled():
        logger.info("otel_disabled")
        return None, None

    try:
        resource = _get_resource()
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"

        # Configure trace provider
        trace_provider = TracerProvider(resource=resource)
        trace_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(trace_provider)

        # Configure metrics provider
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=insecure)
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter, export_interval_millis=60000  # Export every 60 seconds
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)

        logger.info(
            "otel_configured",
            extra={
                "endpoint": endpoint,
                "service": resource.attributes.get(SERVICE_NAME),
                "environment": resource.attributes.get(DEPLOYMENT_ENVIRONMENT),
            },
        )
        return trace_provider, meter_provider

    except Exception as exc:
        logger.error(
            "otel_configuration_failed", extra={"error_type": type(exc).__name__, "error": str(exc)}
        )
        return None, None


def instrument_fastapi(app: Any) -> None:
    """
    Instrument FastAPI application with automatic OTEL tracing.

    Args:
        app: FastAPI application instance
    """
    if not _is_otel_enabled():
        return

    try:
        FastAPIInstrumentor.instrument_app(app)
        logger.info("fastapi_instrumented")
    except Exception as exc:
        logger.warning(
            "fastapi_instrumentation_failed",
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )


def get_meter(name: str = "context-compiler") -> metrics.Meter:
    """
    Get or create a meter for recording metrics.

    Args:
        name: Name of the meter (usually module or component name)

    Returns:
        OpenTelemetry Meter instance
    """
    return metrics.get_meter(name)


def get_tracer(name: str = "context-compiler") -> trace.Tracer:
    """
    Get or create a tracer for recording traces.

    Args:
        name: Name of the tracer (usually module or component name)

    Returns:
        OpenTelemetry Tracer instance
    """
    return trace.get_tracer(name)


# Pipeline-specific metrics
def get_pipeline_metrics() -> dict[str, Any]:
    """
    Get pipeline-specific meters and instruments.

    Returns:
        Dictionary of metric instruments for pipeline monitoring
    """
    meter = get_meter("context-compiler.pipeline")

    return {
        "runs_total": meter.create_counter(
            name="pipeline.runs.total",
            description="Total number of pipeline runs",
            unit="1",
        ),
        "run_duration": meter.create_histogram(
            name="pipeline.run.duration",
            description="Pipeline execution duration",
            unit="ms",
        ),
        "contract_generation": meter.create_histogram(
            name="pipeline.contract.generation_duration",
            description="Contract generation duration",
            unit="ms",
        ),
        "schema_planning": meter.create_histogram(
            name="pipeline.schema.planning_duration",
            description="Schema planning duration",
            unit="ms",
        ),
        "insights_generated": meter.create_counter(
            name="pipeline.insights.generated",
            description="Number of insights generated",
            unit="1",
        ),
        "errors_total": meter.create_counter(
            name="pipeline.errors.total",
            description="Total number of pipeline errors",
            unit="1",
        ),
    }
