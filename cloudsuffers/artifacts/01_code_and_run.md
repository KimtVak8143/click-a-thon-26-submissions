# 1. Code + how to run it

## What is submitted

The submission is the Context Compiler backend for the Atlys track. It contains:

- an Instrumentation Agent that turns a spec and NDJSON sample into a contract, DDL proposal, and deployed ClickHouse schema;
- an Analytics Agent that queries the deployed tables, evaluates context freshness, and writes insight summaries grounded in ClickHouse evidence;
- a Context Agent that maintains the living context layer and records changelog entries as tables or semantics evolve;
- Langfuse-based tracing for each agent run and pipeline stage.

## Key implementation files

- [../context-compiler/app/agents/instrumentation.py](../context-compiler/app/agents/instrumentation.py)
- [../context-compiler/app/agents/analytics.py](../context-compiler/app/agents/analytics.py)
- [../context-compiler/app/agents/context_agent.py](../context-compiler/app/agents/context_agent.py)
- [../context-compiler/app/api/pipeline.py](../context-compiler/app/api/pipeline.py)
- [../context-compiler/app/api/analytics.py](../context-compiler/app/api/analytics.py)
- [../context-compiler/app/core/tracing.py](../context-compiler/app/core/tracing.py)
- [../context-compiler/migrations/075_context_changelog.sql](../context-compiler/migrations/075_context_changelog.sql)

## Run prerequisites

1. Create a ClickHouse Cloud service and a database for the Atlys data.
2. Set the required environment variables in [../context-compiler/.env.example](../context-compiler/.env.example) and copy them to `.env`.
3. Enable Langfuse for trace links.
4. Apply migrations and bootstrap the base context.

## One-command setup

```bash
cp .env.example .env
uv sync
uv run python -m app.clickhouse.migrations
uv run python -m app.cli bootstrap-context --source docs/base_context.md
```

## End-to-end run

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then run a feature package end to end:

```bash
curl -s -X POST http://127.0.0.1:8000/pipeline/run \
  -F "spec=@Atlys/specs/01_express_checkout/spec.md;type=text/markdown" \
  -F "events=@Atlys/specs/01_express_checkout/events.ndjson;type=application/x-ndjson" \
  -F "dry_run=false"
```

## Analytics probe

```bash
curl -s -X POST http://127.0.0.1:8000/analytics/probe \
  -H "Content-Type: application/json" \
  -d '{"question": "Analyze the existing funnel and surface the most important issues, with the why.", "mode": "data"}'
```

## Notes for judges

- The implementation is documented in [../context-compiler/README.md](../context-compiler/README.md) and [../context-compiler/RUN.md](../context-compiler/RUN.md).
- The same pipeline can be exercised for the unseen sixth spec once the package is available.
