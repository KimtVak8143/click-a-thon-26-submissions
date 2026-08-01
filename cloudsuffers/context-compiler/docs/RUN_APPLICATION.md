# Run the complete Context Compiler application

The included launcher configures and starts the local application stack:

- ClickHouse in Docker on ports `8123` and `9000`
- FastAPI backend at `http://127.0.0.1:8000`
- React/Vite frontend at `http://127.0.0.1:5173`

It installs dependencies, applies all ClickHouse migrations, bootstraps the approved base context,
and verifies that the frontend can reach the backend through its development proxy.

## Prerequisites

Install and start these tools:

- Python 3.11 or newer
- `uv` 0.11 or newer
- Node.js 20 or newer with npm
- Docker Desktop or another running Docker engine
- `curl`

## Configure the LLM provider

The compiler needs an OpenAI-compatible structured-output provider to run the pipeline. On its
first run, the launcher creates `.env` from `.env.example` without overwriting an existing file.
Set these values in the root `.env`:

```dotenv
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=replace-with-your-key
LLM_MODEL=gpt-4o-mini
LLM_STRUCTURED_OUTPUT_MODE=json_schema
```

For another OpenAI-compatible provider, use its base URL, key, model, and supported structured
output mode. Never commit `.env`; it is ignored by Git.

The launcher always uses its own local ClickHouse connection for development. Existing remote
ClickHouse settings in `.env` are preserved and temporarily overridden only for commands launched
by the script.

## One-command setup and launch

From the repository root:

```bash
chmod +x scripts/setup-and-run.sh
./scripts/setup-and-run.sh up
```

Then open:

- Dashboard: `http://127.0.0.1:5173`
- Backend API documentation: `http://127.0.0.1:8000/docs`

The dashboard checks the API, ClickHouse, and LLM independently. A complete pipeline run requires
all three indicators to be healthy.

## Service lifecycle

```bash
# Install dependencies, migrate, and bootstrap without launching the web services
./scripts/setup-and-run.sh setup

# Start an already initialized stack
./scripts/setup-and-run.sh start

# Show service and process status
./scripts/setup-and-run.sh status

# Follow backend and frontend logs
./scripts/setup-and-run.sh logs

# Restart all local services
./scripts/setup-and-run.sh restart

# Stop backend, frontend, and local ClickHouse
./scripts/setup-and-run.sh down
```

Runtime PID and log files are stored under `.run/`. The ClickHouse container is named
`context-compiler-clickhouse` and is stopped, not deleted, by `down`.

## Verify manually

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/health/clickhouse
curl --fail http://127.0.0.1:8000/health/llm
curl --fail http://127.0.0.1:5173
```

Run the documented Status Sharing case directly against the backend:

```bash
curl --fail-with-body \
  --form 'spec=@tests/fixtures/generalization/10_recipient_without_user.md;type=text/markdown' \
  --form 'events=@tests/fixtures/status_sharing_events.ndjson;type=application/x-ndjson' \
  --form 'dry_run=true' \
  http://127.0.0.1:8000/pipeline/run
```

Keep `dry_run=true` until the generated DDL has been reviewed.

## Troubleshooting

### Docker is not running

Start Docker Desktop and rerun `./scripts/setup-and-run.sh up`. If ports `8123` or `9000` are
already occupied, stop the conflicting service before creating the local ClickHouse container.

### A web port is already occupied

The launcher uses ports `8000` and `5173`. Stop the existing process, or run
`./scripts/setup-and-run.sh status` to check whether this application is already running.

### LLM health is unavailable

Confirm that all three required LLM settings exist in `.env`, then restart the application. The
health endpoint verifies configuration, provider reachability, and model availability without
performing a generation request.

### Pipeline requests take time

Pipeline generation is synchronous and may run for several minutes when contract repairs are
needed. The frontend keeps the request active and provides a Cancel button. Backend and frontend
logs can be inspected with `./scripts/setup-and-run.sh logs`.

## Optional LibreChat stack

LibreChat is separate from the compiler pipeline. The frontend is the sibling project at
`../ui`. Configure secrets in `../ui/.env` from `../ui/.env.example`, then run:

```bash
docker compose -f ../ui/compose.yaml up -d --build
```

This adds LibreChat at `http://localhost:3080` and a production dashboard at
`http://localhost:4173`. See `../ui/README.md` for the required secrets and registration flow.
