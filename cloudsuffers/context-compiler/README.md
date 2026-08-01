# Context Compiler Backend

Phase 0, Phase 1A, and Phase 2A backend foundation for Context Compiler. This service provides
typed environment configuration, structured JSON logs, optional Langfuse setup, ClickHouse
connectivity, metadata migrations, health endpoints, a streaming NDJSON source profiler, strict
canonical analytics-contract models, and provider-neutral Instrumentation Agent contract
generation. It intentionally contains no DDL generation or execution, event ingestion, Context
Agent, Analytics Agent, or frontend code.

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

Apply all ordered, idempotent migrations to create the configured metadata database and tables:

```bash
uv run python -m app.clickhouse.migrations
```

The runner renders `CONTEXT_COMPILER_CLICKHOUSE_METADATA_DATABASE` separately from the analytical
`CONTEXT_COMPILER_CLICKHOUSE_DATABASE`, then executes each migration in filename order. Every file
contains one idempotent `CREATE` or `ALTER` statement. Context metadata includes:

- `pipeline_runs`
- `analytics_contracts`
- `schema_versions`
- `context_versions`
- `context_sources`
- `context_entities`
- `context_metrics`
- `context_relationships`
- `context_issues`
- `context_changelog`
- `query_evidence`

Bootstrap the byte-exact official context after migrations:

```bash
uv run python -m app.context.bootstrap --source docs/base_context.md
uv run python -m app.cli bootstrap-context --source docs/base_context.md
```

The SHA-derived bootstrap is idempotent. It stores the official source unchanged as provenance,
while prompts receive only a bounded redacted projection. CTX-001 through CTX-010 are typed audit
findings; K1 through K7 remain explicitly unproven hypotheses.

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

## Source profiler

Profile an NDJSON file without loading the complete file into memory:

```bash
uv run python -m app.cli profile \
  --events tests/fixtures/express_checkout_events.ndjson \
  --output /tmp/source_profile.json
```

The profiler computes the SHA-256 while reading each line once. Example values, string lengths,
and distinct-value digests are bounded by the `CONTEXT_COMPILER_PROFILE_*` settings in
`.env.example`. Identifier fields never emit examples. Output arrays are sorted and the profile
contains no generation time or input path, making identical input and settings produce stable
JSON.

The API accepts the same input as multipart form data:

```bash
curl --fail-with-body \
  --form 'events=@tests/fixtures/express_checkout_events.ndjson;type=application/x-ndjson' \
  http://localhost:8000/profiles
```

Uploads must have a safe `.ndjson` filename and fit the configured byte limit. They are copied in
bounded chunks to a temporary file and always deleted after profiling. Files containing malformed
rows receive a structured HTTP `422`; the lower-level profiler and CLI retain malformed-row counts
in their output.

## Analytics contracts

Canonical contract models live in `app/contracts/models.py`. Validate proposed contract data
against its exact source profile with:

```python
contract = AnalyticsContract.model_validate_with_profile(contract_data, source_profile)
```

This applies structural Pydantic validation and cross-model source, event, field, entity, funnel,
metric, dimension, relationship, and currency-safety rules. Direct `model_validate` remains useful
for structural deserialization; use `model_validate_with_profile` at the contract approval gate.

Generate a grounded contract from multipart Markdown and NDJSON uploads:

```bash
curl --fail-with-body \
  --form 'spec=@feature.md;type=text/markdown' \
  --form 'events=@events.ndjson;type=application/x-ndjson' \
  http://localhost:8000/contracts/generate
```

Configure any OpenAI-compatible structured-output endpoint with `LLM_BASE_URL`, `LLM_API_KEY`,
`LLM_MODEL`, `LLM_TIMEOUT_SECONDS`, and `LLM_MAX_RETRIES`. The HTTP client is initialized lazily.
`LLM_STRUCTURED_OUTPUT_MODE` selects `json_object` (the default for Ollama compatibility) or
`json_schema`; `LLM_MAX_OUTPUT_TOKENS`, `LLM_TEMPERATURE`, and
`LLM_TOTAL_GENERATION_TIMEOUT_SECONDS` bound generation behavior. One pooled async client is reused
for generation, repairs, and health checks, then closed during application shutdown.
The agent sends the untrusted specification, a value-redacted aggregate source profile, the compact
`ContractIntent` JSON schema, and bounded optional context. It never sends the complete
`AnalyticsContract` schema or asks the model to reproduce deterministic source metadata.
Presentation-only schema metadata and local-only profile diagnostics are omitted, and raw NDJSON
rows are never sent. A validated intent is deterministically compiled into the complete contract,
then passed through the existing final Pydantic and grounding gates. Invalid intents are returned
to the model with exact safe reference errors for at most two repair attempts; exhausted attempts
return a structured blocked result. `LLM_MAX_OUTPUT_TOKENS` defaults to `2500` for the compact IR.

Before provider invocation, the endpoint resolves the latest approved context. Successful
responses include `context_version_id` and `context_content_sha256`; missing or unavailable
context produces a structured blocked result without an LLM call. Contract 1.0 remains compatible
with historical artifacts because the added provenance/evidence fields have deserialization
defaults, while newly compiled contracts populate explicit workflow grain, attribution,
deduplication, computability, dimensions, and evidence references.

`GET /health/llm` checks configuration, endpoint reachability, and configured-model availability
through `/models` when supported. It never performs generation. Safe structured logs and Langfuse
metadata expose upload, profiling, prompt, provider, parsing, validation, repair, and total timing
without recording prompts, responses, event rows, identifiers, or credentials.

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
- `app/agents`: Instrumentation Agent generation and repair orchestration.
- `app/core`: Pydantic settings, JSON logging, and non-fatal Langfuse lifecycle.
- `app/profiling`: Stable output models and the streaming NDJSON profiler.
- `app/contracts`: Strict canonical analytics-contract models and semantic validation.
- `app/llm`: Provider-neutral structured-generation protocol, OpenAI-compatible adapter, and fake.
- `app/clickhouse`: Connector construction, repositories, and migration runner.
- `app/services`: Application-level health behavior with no connector dependency.
- `app/models`: Typed API response models.
- `migrations`: Ordered ClickHouse DDL, one statement per file.
- `tests`: Phase 0, profiler, contract, CLI, and API tests with synthetic feature fixtures.

Database calls remain behind repository interfaces. API routes depend on services, allowing unit
tests and future orchestration code to avoid direct connector access. IDs are stored as ClickHouse
`UUID`, and all metadata timestamps use `DateTime64(3, 'UTC')`.
