# OpenTelemetry + ClickHouse Integration Guide

This guide shows how to set up OpenTelemetry metrics collection with ClickHouse Cloud as the backend.

---

## 🎯 Architecture

```
FastAPI Backend → OTEL Collector → ClickHouse Cloud
                              ↓
                         Langfuse (traces)
```

**Data Flow:**
- **Traces** → Langfuse (for AI observability)
- **Metrics** → ClickHouse (for analytics & dashboards)

---

## 📋 What's Already Configured

✅ OTEL Collector configuration ([`deploy/otel-collector.yaml`](../deploy/otel-collector.yaml))  
✅ ClickHouse connection settings (`.env`)  
✅ Langfuse tracing integration ([`app/core/tracing.py`](../app/core/tracing.py))

---

## 🚀 Setup Steps

### 1. Add Python OTEL Dependencies

Add to [`pyproject.toml`](../pyproject.toml):

```toml
dependencies = [
    # Existing...
    "opentelemetry-api>=1.27.0",
    "opentelemetry-sdk>=1.27.0",
    "opentelemetry-instrumentation-fastapi>=0.48b0",
    "opentelemetry-exporter-otlp>=1.27.0",
]
```

Then install:
```bash
uv sync
```

### 2. Deploy OTEL Collector on Railway

**Option A: Add as Railway Service**

1. Create new service on Railway
2. Use Docker image: `otel/opentelemetry-collector-contrib:latest`
3. Mount config:
   ```dockerfile
   FROM otel/opentelemetry-collector-contrib:latest
   COPY deploy/otel-collector.yaml /etc/otel-collector-config.yaml
   CMD ["--config=/etc/otel-collector-config.yaml"]
   ```
4. Set environment variables (see below)

**Option B: Use Railway Template**

Use the OpenTelemetry Collector template from Railway marketplace.

### 3. Configure Environment Variables

**In Railway OTEL Collector Service:**

```bash
# ClickHouse connection
CLICKHOUSE_OTEL_ENDPOINT=https://l32d11kq3n.ap-south-1.aws.clickhouse.cloud:8443
CLICKHOUSE_DATABASE=clickathon1
CLICKHOUSE_USERNAME=default
CLICKHOUSE_PASSWORD=Qtr8FmbXI~JFm

# Langfuse connection (if using)
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_OTEL_AUTH_HEADER=Bearer your-langfuse-secret-key
```

**In Railway Backend Service:**

```bash
# OTEL configuration
OTEL_ENABLED=true
OTEL_SERVICE_NAME=context-compiler
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.railway.internal:4317
OTEL_EXPORTER_OTLP_INSECURE=true
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=none
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production,service.namespace=cloudsuffers
```

### 4. Initialize OTEL in FastAPI

Create `app/core/metrics.py`:

```python
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import os

def configure_otel():
    """Configure OpenTelemetry for traces and metrics."""
    
    if not os.getenv("OTEL_ENABLED", "false").lower() == "true":
        return None
    
    # Resource attributes
    resource = Resource(attributes={
        SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "context-compiler"),
        SERVICE_VERSION: "0.1.0",
        "deployment.environment": os.getenv("CONTEXT_COMPILER_APP_ENV", "development"),
    })
    
    # Trace provider
    trace_provider = TracerProvider(resource=resource)
    trace_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        insecure=os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true",
    )
    trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(trace_provider)
    
    # Metrics provider
    metric_exporter = OTLPMetricExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        insecure=os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true",
    )
    metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    
    return trace_provider, meter_provider


def instrument_fastapi(app):
    """Instrument FastAPI with automatic OTEL tracing."""
    if os.getenv("OTEL_ENABLED", "false").lower() == "true":
        FastAPIInstrumentor.instrument_app(app)
```

Update `app/main.py`:

```python
from app.core.metrics import configure_otel, instrument_fastapi

# In startup
@app.on_event("startup")
async def startup():
    # Existing startup code...
    
    # Configure OTEL
    configure_otel()
    instrument_fastapi(app)
```

---

## 📊 ClickHouse Schema for Metrics

OTEL Collector automatically creates tables in ClickHouse:

```sql
-- Metrics table (auto-created)
otel_metrics
  - MetricName
  - Timestamp
  - Value
  - Attributes (Map(String, String))
  - ResourceAttributes (Map(String, String))
  - ScopeName
  - ScopeVersion

-- Traces table (if traces to ClickHouse enabled)
otel_traces
  - TraceId
  - SpanId
  - ParentSpanId
  - TraceState
  - SpanName
  - SpanKind
  - ServiceName
  - ResourceAttributes
  - Timestamp
  - Duration
  - StatusCode
  - StatusMessage
```

---

## 📈 Example Metrics Queries

### Request Rate
```sql
SELECT
    toStartOfMinute(Timestamp) AS minute,
    count() AS requests
FROM otel_metrics
WHERE MetricName = 'http.server.request.duration'
  AND ResourceAttributes['service.name'] = 'context-compiler'
GROUP BY minute
ORDER BY minute DESC
LIMIT 100;
```

### Error Rate
```sql
SELECT
    toStartOfHour(Timestamp) AS hour,
    countIf(Attributes['http.status_code'] >= '400') / count() AS error_rate
FROM otel_metrics
WHERE MetricName = 'http.server.request.duration'
GROUP BY hour
ORDER BY hour DESC;
```

### P95 Latency
```sql
SELECT
    ResourceAttributes['service.name'] AS service,
    quantile(0.95)(Value) AS p95_latency_ms
FROM otel_metrics
WHERE MetricName = 'http.server.request.duration'
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY service;
```

### Pipeline Duration Metrics
```sql
SELECT
    Attributes['feature_slug'] AS feature,
    avg(Value) AS avg_duration_ms,
    quantile(0.50)(Value) AS p50,
    quantile(0.95)(Value) AS p95,
    quantile(0.99)(Value) AS p99
FROM otel_metrics
WHERE MetricName = 'pipeline.run.duration'
  AND Timestamp >= now() - INTERVAL 24 HOUR
GROUP BY feature
ORDER BY avg_duration_ms DESC;
```

---

## 🔍 Custom Metrics

Add custom metrics in your code:

```python
from opentelemetry import metrics

# Get meter
meter = metrics.get_meter("context-compiler")

# Create counter
pipeline_runs = meter.create_counter(
    "pipeline.runs.total",
    description="Total pipeline runs",
    unit="1"
)

# Create histogram
pipeline_duration = meter.create_histogram(
    "pipeline.run.duration",
    description="Pipeline execution duration",
    unit="ms"
)

# Record metrics
pipeline_runs.add(1, {"feature": feature_slug, "status": "completed"})
pipeline_duration.record(duration_ms, {"feature": feature_slug})
```

---

## 🐛 Troubleshooting

### OTEL Collector not receiving data
```bash
# Check collector logs
railway logs --service otel-collector

# Test endpoint
curl -v http://otel-collector.railway.internal:4317
```

### ClickHouse not receiving metrics
```bash
# Check ClickHouse tables
SELECT count() FROM otel_metrics;
SELECT count() FROM otel_traces;

# Check recent data
SELECT * FROM otel_metrics ORDER BY Timestamp DESC LIMIT 10;
```

### Backend not sending data
```bash
# Check backend logs for OTEL errors
railway logs --service context-compiler | grep -i otel

# Verify environment variables
railway variables --service context-compiler | grep OTEL
```

---

## 📚 Resources

- [OpenTelemetry Python Docs](https://opentelemetry.io/docs/languages/python/)
- [ClickHouse OTEL Integration](https://clickhouse.com/docs/integrations/opentelemetry)
- [OTEL Collector Contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib)
- [Langfuse OTEL](https://langfuse.com/docs/integrations/opentelemetry)

---

## ✅ Deployment Checklist

- [ ] Add OTEL dependencies to pyproject.toml
- [ ] Create app/core/metrics.py
- [ ] Update app/main.py with OTEL initialization
- [ ] Deploy OTEL Collector to Railway
- [ ] Set environment variables in Railway
- [ ] Test metrics collection
- [ ] Create ClickHouse dashboards
- [ ] Set up alerts (optional)

---

**Estimated Setup Time**: 30-45 minutes
