# UI Integration Plan

## Architecture boundary

The FastAPI backend stays unchanged. The React dashboard reads its public API through an Nginx same-origin proxy, and LibreChat runs as a separate Docker service with isolated persistence. This avoids adding CORS, authentication, chat, or presentation logic to the backend.

```text
Browser
  |-- :4173 React dashboard -- /compiler-api/* --> Nginx --> host:8000 FastAPI
  `-- :3080 LibreChat --> configured model provider
                              |-- MongoDB (conversations)
                              `-- Meilisearch (message search)
```

## Phase 1 — integration baseline (implemented)

- Responsive React run-inspector shell.
- API and ClickHouse connection status.
- NDJSON upload and source-profile summary using `POST /profiles`.
- Pipeline and insight empty states that do not invent unavailable data.
- LibreChat Docker service using user-provided model keys.
- Navigation between dashboard and chat.
- No backend files or routes changed.

## Phase 2 — run inspector

Bind the dashboard when these read/write APIs become available:

| UI surface | Expected API contract |
|---|---|
| New Run | `POST /runs` with spec, events, and execution mode |
| Run list | `GET /runs` |
| Timeline | `GET /runs/{run_id}` with stages, status, duration, warnings |
| Instrumentation | `GET /runs/{run_id}/contract` and `/schema` |
| Context Diff | `GET /runs/{run_id}/context-diff` |
| Insights | `GET /runs/{run_id}/insights` |
| Evidence drawer | `GET /evidence/{evidence_id}` |

Prefer polling the run resource every 2–3 seconds during execution; move to server-sent events only if polling becomes a demonstrated bottleneck.

## Phase 3 — evidence-aware chat

Do not send raw NDJSON to LibreChat. Add one of these integrations after the backend supports it:

1. An MCP server exposing read-only tools such as `get_run`, `list_insights`, and `get_evidence` (preferred).
2. LibreChat Actions backed by a narrow OpenAPI document.
3. An OpenAI-compatible Context Compiler gateway if chat itself becomes part of the orchestrator.

Every chat tool response should carry `run_id`, `context_version`, and `evidence_id`. LibreChat may explain evidence, but verified insight cards and exact SQL remain dashboard-owned.

## Phase 4 — demo hardening

- Deep-link dashboard insight cards to a prefilled LibreChat conversation.
- Add contract/schema diff views and a SQL evidence drawer.
- Persist the selected run in the URL.
- Add loading, retry, blocked-stage, and stale-context states.
- Add UI smoke tests for profile upload and the run timeline.
- Pin container and npm dependency versions before submission.

## Acceptance criteria

- The dashboard works without LibreChat and LibreChat works without the dashboard.
- No raw event data or provider secrets are committed or sent to a model.
- A failed backend dependency is visible without crashing the UI.
- No numerical claim is rendered without an evidence identifier.
- The UI never marks a pipeline stage green unless the backend reports it as validated or published.
