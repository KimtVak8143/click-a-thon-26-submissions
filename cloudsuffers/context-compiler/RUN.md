# RUN.md — Context Compiler

How to run the three agents (Instrumentation, Analytics, Context), their tracing,
and the optional LibreChat/MCP integration, end to end against a real ClickHouse
Cloud service.

For a fully local dev stack (Docker-hosted ClickHouse, hot-reloading frontend), see
[`docs/RUN_APPLICATION.md`](docs/RUN_APPLICATION.md) instead — this file targets the
graded, ClickHouse-Cloud-backed run.

## 1. Environment variables

Copy `.env.example` to `.env` and set:

```dotenv
# Your team's ClickHouse Cloud service (no shared instance — see PROBLEM_STATEMENT.md)
CONTEXT_COMPILER_CLICKHOUSE_HOST=<your-service>.clickhouse.cloud
CONTEXT_COMPILER_CLICKHOUSE_PORT=8443
CONTEXT_COMPILER_CLICKHOUSE_SECURE=true
CONTEXT_COMPILER_CLICKHOUSE_USERNAME=default
CONTEXT_COMPILER_CLICKHOUSE_PASSWORD=<your-password>
CONTEXT_COMPILER_CLICKHOUSE_DATABASE=<your-analytics-database>

# LLM provider (any OpenAI-compatible provider/key)
CONTEXT_COMPILER_LLM_BASE_URL=https://api.openai.com/v1
CONTEXT_COMPILER_LLM_API_KEY=<your-key>
CONTEXT_COMPILER_LLM_MODEL=gpt-4o-mini
CONTEXT_COMPILER_LLM_STRUCTURED_OUTPUT_MODE=json_schema

# Langfuse (tracing — required for graded trace links)
CONTEXT_COMPILER_LANGFUSE_ENABLED=true
CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY=<pk-lf-...>
CONTEXT_COMPILER_LANGFUSE_SECRET_KEY=<sk-lf-...>
CONTEXT_COMPILER_LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
```

Apply ClickHouse migrations once against that service (metadata tables — schema
versions, context versions/changelog, pipeline runs, query evidence, insights),
then bootstrap the approved base context:

```bash
uv run python -m app.clickhouse.migrations
uv run python -m app.cli bootstrap-context
```

## 2. One command to run the full pipeline end to end

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then, for any of the five known specs or the sixth (unseen) spec:

```bash
curl -s -X POST http://127.0.0.1:8000/pipeline/run \
  -F "spec=@Atlys/specs/01_express_checkout/spec.md;type=text/markdown" \
  -F "events=@Atlys/specs/01_express_checkout/events.ndjson;type=application/x-ndjson" \
  -F "dry_run=false"
```

This runs Instrumentation → Schema Planner → **event ingestion** (loads the uploaded
NDJSON into the deployed table so there's real data to analyze) → Context Agent →
Analytics Agent, in one request, fully traced. Use `dry_run=true` to preview the
generated DDL without deploying or loading data (no insights will be generated from
real evidence in that mode, since the table doesn't exist yet).

Response includes `run_id`, `status`, the generated `contract`, `schema_plan.ddl`,
`context_version_id`, and `insights`. Every stage is wrapped in a Langfuse trace
under that `run_id`.

## 3. Ask the Analytics Agent an open-ended question

For the four standard probes (and the "unseen spec" bundle's insight summary), use
the probe endpoint instead of the fixed per-feature run — it discovers every
event-shaped table the context layer knows about (or can structurally find) and
answers grounded in real aggregate query results:

```bash
curl -s -X POST http://127.0.0.1:8000/analytics/probe \
  -H "Content-Type: application/json" \
  -d '{"question": "Analyze the existing funnel and surface the most important issues, with the why.", "mode": "data"}'
```

For the context self-audit probe ("is anything in the base context wrong, stale, or
self-contradictory?"), pass `"mode": "context_audit"` — this skips ClickHouse
entirely and asks the model to critique the approved context's own declared content.

Every probe call gets its own Langfuse trace tagged `analytics-agent`, `probe`.

## 4. Context layer + freshness proof

The context layer is a versioned ClickHouse table chain (`context_versions` +
`context_changelog` in the metadata database), not a file — chosen so every pipeline
run can read the latest approved version transactionally and every change is
independently queryable/auditable, rather than re-parsing a document each time.
`GET /dashboard` returns `context_changelog` (the before/after entries) directly;
each pipeline run's Context Agent step appends one entry when a new feature table is
registered.

## 5. Optional: LibreChat + two MCP servers

LibreChat (`../ui`) is wired to **two** MCP servers, for two different jobs:

- **`clickhouse`** — the official
  [ClickHouse MCP server](https://github.com/ClickHouse/mcp-clickhouse), generic
  read-only SQL tools (`list_databases`, `list_tables`, `run_query`) against the
  same ClickHouse Cloud service. It has no idea what a "feature", "contract", or
  "context version" is — it just runs whatever SQL the model decides to write.
- **`context-compiler`** — a small first-party MCP server (`app/mcp_server.py`)
  wrapping the backend's own `/analytics/probe` and `/dashboard` endpoints, so chat
  answers actually go through the Analytics Agent, grounded in the current context
  layer and the most recent pipeline runs — the same thing this doc's section 2-3
  do over curl, just reachable as MCP tools. Prefer this one for anything about a
  feature's funnel, conversions, trends, or context freshness.

Start the backend and this second MCP server on the host (both need to already be
running before LibreChat starts, or it just falls back to "clickhouse" only):

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 &
uv run python -m app.mcp_server &
```

Then, from `../ui`:

```bash
cp .env.example .env   # fill in MCP_CLICKHOUSE_*
docker compose up -d
```

Open `http://localhost:3080`; both tools appear in LibreChat's chat menu. Auth is
disabled on both MCP servers (`CLICKHOUSE_MCP_AUTH_DISABLED=true` for
`mcp-clickhouse`; `context-compiler`'s own server has none) — LibreChat's MCP client
permanently misreads a static-bearer-token 401 as an OAuth requirement, so a token
doesn't actually work here. Local/demo only: don't expose ports `8001`/`8002` beyond
your own machine. Write access stays off regardless
(`CLICKHOUSE_ALLOW_WRITE_ACCESS` is never set), so `clickhouse` is read-only no
matter what.

## Troubleshooting

- **Pipeline run is slow / hits `contract_blocked`**: contract generation includes up
  to 3 automatic repair attempts before giving up; this is expected for specs the
  model doesn't get right on the first try. See `errors` in the response for the
  specific validation failures.
- **Insights say "no data" / "not ready"**: the run used `dry_run=true` (no table
  deployed, nothing to query) — rerun with `dry_run=false`.
- **`/analytics/probe` returns 422 `no_approved_context`**: run migrations and bootstrap
  the base context first (`uv run python -m app.cli bootstrap-context`, or see
  `docs/RUN_APPLICATION.md`).
