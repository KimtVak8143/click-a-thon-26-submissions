# 4. Langfuse trace links

Every run should be linked back to the Langfuse trace so judges can inspect the full chain of reasoning.

## What to include

For each run, capture:

- the run identifier;
- the root trace URL or trace ID;
- the agent stage that produced the artifact;
- a short note explaining the output being validated.

## Suggested tracking table

- Instrumentation run for known spec: [surprise_round/known_specs_ddl.md](surprise_round/known_specs_ddl.md)
- Analytics run over existing tables: [surprise_round/analytics_insight_report.md](surprise_round/analytics_insight_report.md)
- Context freshness run: [surprise_round/context_freshness.md](surprise_round/context_freshness.md)
- Sixth-spec run: [surprise_round/sixth_spec_bundle.md](surprise_round/sixth_spec_bundle.md)

## Trace-link template

```text
Run: <run_id>
Trace: <langfuse_trace_url>
Stage: instrumentation | analytics | context
Artifact: <ddl | insight report | context diff | sixth-spec bundle>
```

## Notes

Langfuse is enabled through [../context-compiler/app/core/tracing.py](../context-compiler/app/core/tracing.py) and the environment variables documented in [../context-compiler/RUN.md](../context-compiler/RUN.md).
