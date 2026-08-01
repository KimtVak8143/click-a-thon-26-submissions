# Context Compiler Backend

Phase 0 and the initial deterministic backend foundation for Context Compiler. This service
provides typed environment configuration, structured JSON logs, optional Langfuse setup,
ClickHouse connectivity, metadata migrations, and health endpoints. It intentionally contains no
agents, LLM prompts, profiling, ingestion, or analytics logic yet.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) 0.11 or newer
- A reachable ClickHouse server (24.x or newer is recommended)

## Setup

Run these commands from the repository root:

```bash
cp .env.example .env
uv sync
```

For a local ClickHouse instance using Docker:

```bash
docker run --name context-compiler-clickhouse \
  --detach \
  --publish 8123:8123 \
  --publish 9000:9000 \
  clickhouse/clickhouse-server:latest
```

The defaults in `.env.example` connect to that local HTTP endpoint. For ClickHouse Cloud, set the
host, HTTPS port, secure flag, username, and password in `.env`:

```dotenv
CONTEXT_COMPILER_CLICKHOUSE_HOST=your-host.clickhouse.cloud
CONTEXT_COMPILER_CLICKHOUSE_PORT=8443
CONTEXT_COMPILER_CLICKHOUSE_SECURE=true
CONTEXT_COMPILER_CLICKHOUSE_USERNAME=your-username
CONTEXT_COMPILER_CLICKHOUSE_PASSWORD=your-password
```

No ClickHouse credentials are stored in source control.

## Migrations

Apply all ordered, idempotent migrations to create `compiler_meta` and its initial tables:

```bash
uv run python -m app.clickhouse.migrations
```

The runner executes each `migrations/*.sql` file in filename order. Each file contains one
`CREATE ... IF NOT EXISTS` statement, so rerunning the command is safe. The initial tables are:

- `pipeline_runs`
- `analytics_contracts`
- `schema_versions`
- `context_versions`
- `context_issues`
- `query_evidence`

## Run the API

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify the service and ClickHouse separately:

```bash
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/health/clickhouse
```

`GET /health` checks the API process without contacting external systems. `GET
/health/clickhouse` performs a bounded ClickHouse ping and returns HTTP `503` when unavailable.

## Langfuse

Langfuse is disabled by default. To configure it, set all three values in `.env`:

```dotenv
CONTEXT_COMPILER_LANGFUSE_ENABLED=true
CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY=pk-lf-...
CONTEXT_COMPILER_LANGFUSE_SECRET_KEY=sk-lf-...
CONTEXT_COMPILER_LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

Missing credentials leave tracing disabled. Client initialization and shutdown failures are
logged as structured events and do not prevent application startup or shutdown. Credentials are
never logged.

## Quality checks

Format, lint, and run the unit tests:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
```

## Architecture

- `app/api`: FastAPI routes and dependency adapters.
- `app/core`: Pydantic settings, JSON logging, and non-fatal Langfuse lifecycle.
- `app/clickhouse`: Connector construction, repositories, and migration runner.
- `app/services`: Application-level health behavior with no connector dependency.
- `app/models`: Typed API response models.
- `migrations`: Ordered ClickHouse DDL, one statement per file.
- `tests`: Configuration and health endpoint unit tests using repository test doubles.

Database calls remain behind repository interfaces. API routes depend on services, allowing unit
tests and future orchestration code to avoid direct connector access. IDs are stored as ClickHouse
`UUID`, and all metadata timestamps use `DateTime64(3, 'UTC')`.
