# Langfuse Integration Guide

## Overview

Context Compiler uses **Langfuse** for comprehensive observability of all agent activities, LLM generations, and analytical workflows. This integration follows Langfuse best practices for tracing, observation types, token tracking, and metadata.

## Key Features

### 1. **Proper Observation Types**
- ✅ **Generations**: LLM calls use `generation` type with token usage
- ✅ **Agents**: Agent executions use `agent` type (Instrumentation, Analytics, Context)
- ✅ **Spans**: Supporting operations use appropriate span types
- ✅ **Hierarchical Structure**: Nested observations show execution flow

### 2. **Comprehensive Metadata**
- **Feature Tags**: Each trace tagged with feature name (`instrumentation`, `analytics`, `context`)
- **Run IDs**: Every pipeline run has unique ID linking all traces
- **Model Information**: Tracks which LLM model was used
- **Token Usage**: Input/output tokens tracked for cost analysis
- **Context Versions**: Links traces to specific context versions

### 3. **Input/Output Tracking**
- **Masked Sensitive Data**: API keys, passwords, tokens automatically masked
- **Relevant Inputs**: Only meaningful data captured (user queries, not internal config)
- **Structured Outputs**: Results captured with counts and summaries

### 4. **Error Handling**
- **Never Breaks Generation**: Tracing failures never stop LLM calls
- **Graceful Degradation**: Falls back to NullTracer if Langfuse unavailable
- **Error Status Tracking**: Failed observations marked with ERROR level

## Configuration

### Environment Variables

```bash
# Enable Langfuse
CONTEXT_COMPILER_LANGFUSE_ENABLED=true

# Langfuse credentials (from project settings → API Keys)
CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY=pk-lf-...
CONTEXT_COMPILER_LANGFUSE_SECRET_KEY=sk-lf-...

# Langfuse server (cloud or self-hosted)
CONTEXT_COMPILER_LANGFUSE_BASE_URL=https://cloud.langfuse.com  # EU
# or
CONTEXT_COMPILER_LANGFUSE_BASE_URL=https://us.cloud.langfuse.com  # US
```

### Getting Langfuse Keys

1. Sign up at [langfuse.com/cloud](https://langfuse.com/cloud) (free tier available)
2. Create a project
3. Go to **Settings → API Keys**
4. Create a new API key pair
5. Copy both public (pk-lf-...) and secret (sk-lf-...) keys to `.env`

## Architecture

### Tracer Hierarchy

```
run-pipeline (chain, trace_id: run_id)
│
├─ profile-source (span)
├─ retrieve-approved-context (retriever)
├─ total_generation (span)
│  ├─ prompt_construction (span)
│  ├─ instrumentation_agent (agent)
│  │  ├─ intent_generation (generation) ← LLM call with token usage
│  │  ├─ JSON_parsing (span)
│  │  ├─ validation (span)
│  │  └─ intent_repair (generation) ← Repair attempt if needed
│
├─ plan-schema (span)
├─ update-context (agent)
│
└─ analytics_agent (agent)
   ├─ query_execution (span)
   │  ├─ query_baseline_funnel (tool)
   │  ├─ query_weekly_trend_purchases (tool)
   │  ├─ query_top_segments_device_type (tool)
   │  └─ query_feature_table_ready (tool)
   └─ insight_generation (generation) ← LLM call with token usage
```

### Observation Types Explained

| Type | Usage | Token Tracking | Examples |
|------|-------|----------------|----------|
| `agent` | Multi-step autonomous agent execution | No | InstrumentationAgent, AnalyticsAgent |
| `chain` | End-to-end workflow orchestration | No | Pipeline run |
| `generation` | Direct LLM API call | **Yes** | Contract generation, insight generation |
| `span` | Supporting operation or computation | No | Parsing, validation, queries |
| `tool` | Database/tool invocation | No | ClickHouse analytics query |
| `retriever` | Context/data lookup (future use) | No | Context retrieval, vector search |

## Usage Patterns

### Creating a Traced Agent Operation

```python
from app.core.tracing import SafeLangfuseInstrumentationTracer

# Create tracer with feature name and tags
tracer = SafeLangfuseInstrumentationTracer(
    langfuse_client,
    trace_id=run_id.hex,
    feature_name="contract_generation",
    tags=["api", "instrumentation-agent"],
)

# Trace an agent execution
with tracer.observe(
    "instrumentation_agent",
    as_type="agent",
    input={"feature_slug": feature_slug, "event_count": row_count},
    metadata={"run_id": str(run_id), "model": model_name},
    tags=["instrumentation"],
) as agent_obs:
    # ... agent logic ...
    agent_obs.update(
        output={"contract_status": "valid"},
        metadata={"validation_attempts": 1},
    )
```

### Tracking LLM Generations

```python
# Track LLM generation with token usage
with tracer.observe(
    "intent_generation",
    as_type="generation",
    input={"attempt": 1, "schema_name": "ContractIntent"},
    metadata={"stage": "generation", "attempt_number": 1},
    model=provider.model_name,
    tags=["generation", "attempt-1"],
) as gen_obs:
    response = await provider.generate(request)
    
    # Update with output and token usage
    gen_obs.update(
        output={"response_bytes": len(response.content)},
        model=response.model,
        usage_details=response.usage.as_langfuse(),  # {"input": 1234, "output": 567}
    )
```

### Tracking Database Queries

```python
with tracer.observe(
    "query_baseline_funnel",
    as_type="span",
    input={"metric_name": "baseline_funnel"},
    metadata={"query_type": "analytics"},
    tags=["clickhouse-query"],
) as query_obs:
    result = client.query(sql, parameters=params)
    
    query_obs.update(
        output={"rows_returned": len(result)},
        metadata={"latency_ms": elapsed_ms},
    )
```

## Viewing Traces

### Verify authenticated connectivity

The regular test suite never sends telemetry. After configuring `.env`, run the opt-in live
test to authenticate, export a development trace, flush it, and fetch it back through the API:

```bash
RUN_LANGFUSE_LIVE_TEST=1 uv run pytest -q \
  tests/test_langfuse_integration.py::test_live_langfuse_connectivity
```

### 1. Langfuse UI

Access your traces at `https://cloud.langfuse.com` (or your self-hosted URL):

- **Traces View**: See all pipeline runs
- **Filter by Tags**: Filter by `feature:contract_generation`, `instrumentation`, etc.
- **Trace View**: One trace per pipeline run, correlated with `run_id` metadata
- **Generations View**: Analyze LLM calls, tokens, and costs
- **Agent Graph**: Visualize agent execution flow

### 2. CLI Access

Query traces programmatically:

```bash
# Setup
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://cloud.langfuse.com

# List recent traces
npx langfuse-cli api trace list --limit 10

# Get specific trace
npx langfuse-cli api trace get --traceId <trace-id>

# List all generations
npx langfuse-cli api generation list --limit 20
```

## Best Practices Implemented

Based on [Langfuse observability best practices](https://langfuse.com/docs/observability/best-practices):

### ✅ Baseline Requirements Met

- [x] **Model name tracked**: Every generation captures the model used
- [x] **Token usage tracked**: Input/output tokens for all LLM calls
- [x] **Descriptive names**: `contract-generation`, `intent_generation`, not `trace-1`
- [x] **Span hierarchy**: Nested observations show which step failed
- [x] **Correct types**: Generations are `generation`, agents are `agent`
- [x] **Sensitive data masked**: API keys, secrets automatically filtered
- [x] **Relevant input/output**: Only meaningful data, not all function args

### ✅ Contextual Metadata

- [x] **Feature tags**: `instrumentation`, `analytics`, `context`
- [x] **Run IDs**: Pipeline runs grouped by `run_id`
- [x] **Context version IDs**: Links to specific business context
- [x] **Attempt tracking**: Repair loops numbered (attempt-1, attempt-2)

### ✅ Error Handling

- [x] **Never breaks generation**: Tracing errors caught and logged
- [x] **Error status tracking**: Failed observations marked ERROR
- [x] **Status messages**: Error details captured for debugging

## Monitoring & Debugging

### Key Metrics to Track

1. **Token Usage by Feature**
   - Filter generations by tag (`instrumentation`, `analytics`)
   - Sum input/output tokens
   - Calculate costs per feature

2. **Generation Success Rate**
   - Count generations by status
   - Track repair loop frequency
   - Identify failing patterns

3. **Latency Analysis**
   - Compare generation vs query time
   - Identify slow operations
   - Optimize bottlenecks

4. **Cost Attribution**
   - Cost per pipeline run
   - Cost by agent (instrumentation vs analytics)
   - Model comparison (if using multiple)

### Common Debugging Queries

**Find failed generations:**
```sql
-- In Langfuse SQL explorer
SELECT name, input, output, level, status_message
FROM observations
WHERE type = 'generation' AND level = 'ERROR'
ORDER BY start_time DESC
LIMIT 20;
```

**Analyze token usage:**
```sql
SELECT 
  name,
  model,
  SUM(usage_input) as total_input_tokens,
  SUM(usage_output) as total_output_tokens,
  COUNT(*) as call_count
FROM observations
WHERE type = 'generation'
GROUP BY name, model;
```

## Testing Locally

### 1. Run with Langfuse enabled

```bash
# Set credentials in .env
export CONTEXT_COMPILER_LANGFUSE_ENABLED=true
export CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY=pk-lf-...
export CONTEXT_COMPILER_LANGFUSE_SECRET_KEY=sk-lf-...

# Start the API
uv run uvicorn app.main:app --reload
```

### 2. Generate a trace

```bash
# Upload spec and events
curl -X POST http://localhost:8000/pipeline/run \
  -F "spec=@spec.md" \
  -F "events=@events.ndjson"
```

### 3. View in Langfuse

1. Go to `https://cloud.langfuse.com`
2. Select your project
3. View **Traces** tab
4. Find your trace by `run_id` or timestamp
5. Explore nested observations and token usage

### 4. Verify with CLI

```bash
# Get recent traces
npx langfuse-cli api trace list --limit 1

# Get specific trace details
npx langfuse-cli api trace get --traceId <trace-id>
```

## Troubleshooting

### Traces not appearing

**Check 1: Credentials**
```bash
# Verify keys are set
echo $CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY
echo $CONTEXT_COMPILER_LANGFUSE_SECRET_KEY

# Check if Langfuse is enabled
grep LANGFUSE_ENABLED .env
```

**Check 2: Network**
```bash
# Test connection to Langfuse
curl -I https://cloud.langfuse.com
```

**Check 3: Application logs**
```bash
# Look for Langfuse initialization
# Should see: "langfuse_configured" log
# Should NOT see: "langfuse_initialization_failed"
grep langfuse logs/app.log
```

### Traces incomplete

- **Wait for flush**: Traces sent asynchronously, may take 5-10 seconds
- **Check shutdown**: Ensure `langfuse.shutdown()` called on app exit
- **Look for errors**: Check logs for `langfuse_observation_*_failed`

### Token usage missing

- Verify LLM provider returns token counts
- Check `response.usage` is not None
- Ensure `as_langfuse()` method formats correctly

## Integration Checklist

Before deploying:

- [ ] Langfuse credentials configured
- [ ] All agents use tracer (Instrumentation, Analytics, Context)
- [ ] LLM generations marked as `generation` type
- [ ] Token usage tracked on all generations
- [ ] Feature tags added to all operations
- [ ] Sensitive data masked (no API keys in traces)
- [ ] Test traces appear in Langfuse UI
- [ ] CLI access verified
- [ ] Error handling tested (graceful degradation)
- [ ] Flush on shutdown confirmed

## Additional Resources

- [Langfuse Docs](https://langfuse.com/docs)
- [Best Practices Guide](https://langfuse.com/docs/observability/best-practices)
- [Python SDK Reference](https://langfuse.com/docs/sdk/python)
- [CLI Documentation](https://langfuse.com/docs/cli)
- [Agent Graph Guide](https://langfuse.com/docs/observability/features/agent-graphs)
