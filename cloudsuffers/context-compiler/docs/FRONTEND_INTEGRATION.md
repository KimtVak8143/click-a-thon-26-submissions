# Frontend Integration Guide

This guide connects a frontend to the Context Compiler backend and runs one complete existing
feature case through `POST /pipeline/run`.

The recommended first case is Visa Status Sharing:

- Specification: `tests/fixtures/generalization/10_recipient_without_user.md`
- Existing events: `tests/fixtures/status_sharing_events.ndjson`
- Expected feature slug: `recipient_status_sharing`

## 1. Start the backend

The pipeline requires a configured LLM, ClickHouse, migrations, and an approved context version.
From the backend repository root:

```bash
uv run python -m app.clickhouse.migrations
uv run python -m app.cli bootstrap-context --source docs/base_context.md
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify the dependencies before connecting the frontend:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/health/clickhouse
curl --fail http://127.0.0.1:8000/health/llm
```

## 2. Verify the case without the frontend

```bash
curl --fail-with-body \
  --form 'spec=@tests/fixtures/generalization/10_recipient_without_user.md;type=text/markdown' \
  --form 'events=@tests/fixtures/status_sharing_events.ndjson;type=application/x-ndjson' \
  --form 'dry_run=true' \
  http://127.0.0.1:8000/pipeline/run \
  | uv run python -m json.tool
```

Keep `dry_run=true` in the frontend until users have reviewed the generated DDL. The full
orchestration still runs, but schema DDL is planned rather than deployed.

## 3. Configure browser access

The backend permits direct browser requests from Vite's default local origins. Configure a
comma-separated list for any other frontend origin:

```dotenv
CONTEXT_COMPILER_CORS_ALLOWED_ORIGINS=http://localhost:3000,https://frontend.example.com
CONTEXT_COMPILER_CORS_ALLOW_CREDENTIALS=false
```

Restart the backend after changing the environment. Keep credentials disabled unless the
application uses cookie-based authentication. Do not use a wildcard origin in production.

A same-origin proxy remains the recommended production topology and avoids environment-specific
API URLs in frontend code.

For Vite, add this to `vite.config.ts`:

```ts
import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
```

The frontend can then call `/api/pipeline/run`. In production, configure the web server or API
gateway to route the same path to this backend.

## 4. Frontend request and response types

The endpoint accepts `multipart/form-data` with these fields:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `spec` | Markdown file | Yes | Filename must end in `.md` or `.markdown`. |
| `events` | NDJSON file | Yes | Filename must end in `.ndjson`. |
| `dry_run` | Boolean string | No | Use `"true"` while reviewing generated DDL. |

Use these minimal TypeScript types at the frontend boundary:

```ts
export type PipelineStatus = "completed" | "contract_blocked" | "error" | string;

export interface PipelineSchemaPlan {
  strategy: string;
  table_name: string;
  ddl: string;
  deployed: boolean;
}

export interface PipelineInsight {
  title: string;
  summary: string;
  confidence: number;
  category: string;
}

export interface PipelineRunResponse {
  run_id: string;
  feature_slug: string;
  status: PipelineStatus;
  contract: Record<string, unknown> | null;
  schema_plan: PipelineSchemaPlan | null;
  context_version_id: string | null;
  insights: PipelineInsight[] | null;
  errors: string[];
  duration_ms: number;
}

export interface PipelineHttpError {
  detail: {
    code: string;
    message: string;
  };
}
```

Do not manually set the `Content-Type` header when sending `FormData`; the browser must add the
multipart boundary.

## 5. API client

```ts
export async function runPipeline(
  spec: File,
  events: File,
  dryRun = true,
  signal?: AbortSignal,
): Promise<PipelineRunResponse> {
  const form = new FormData();
  form.append("spec", spec);
  form.append("events", events);
  form.append("dry_run", String(dryRun));

  const response = await fetch("/api/pipeline/run", {
    method: "POST",
    body: form,
    signal,
  });

  const body: PipelineRunResponse | PipelineHttpError = await response.json();
  if (!response.ok) {
    const error = body as PipelineHttpError;
    throw new Error(`${error.detail.code}: ${error.detail.message}`);
  }

  return body as PipelineRunResponse;
}
```

Generation is synchronous and can take several minutes when repairs are needed. Keep the UI in a
loading state and provide a cancel button backed by `AbortController`; do not impose a short
browser timeout.

## 6. Example upload component

```tsx
import { FormEvent, useRef, useState } from "react";
import { runPipeline, type PipelineRunResponse } from "./pipeline-api";

export function PipelineRunner() {
  const [result, setResult] = useState<PipelineRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const controller = useRef<AbortController | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const spec = form.get("spec");
    const events = form.get("events");
    if (!(spec instanceof File) || !(events instanceof File)) return;

    controller.current = new AbortController();
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await runPipeline(spec, events, true, controller.current.signal));
    } catch (cause) {
      if ((cause as Error).name !== "AbortError") {
        setError((cause as Error).message);
      }
    } finally {
      setRunning(false);
      controller.current = null;
    }
  }

  return (
    <section>
      <form onSubmit={submit}>
        <label>
          Feature specification
          <input name="spec" type="file" accept=".md,.markdown,text/markdown" required />
        </label>
        <label>
          Event sample
          <input name="events" type="file" accept=".ndjson,application/x-ndjson" required />
        </label>
        <button disabled={running} type="submit">
          {running ? "Running pipeline…" : "Generate contract"}
        </button>
        {running && (
          <button type="button" onClick={() => controller.current?.abort()}>
            Cancel
          </button>
        )}
      </form>

      {error && <p role="alert">{error}</p>}
      {result && <PipelineResult result={result} />}
    </section>
  );
}

function PipelineResult({ result }: { result: PipelineRunResponse }) {
  if (result.status !== "completed") {
    return (
      <section>
        <h2>Pipeline blocked</h2>
        <p>Run: {result.run_id}</p>
        <ul>{result.errors.map((item) => <li key={item}>{item}</li>)}</ul>
      </section>
    );
  }

  return (
    <section>
      <h2>{result.feature_slug}</h2>
      <p>Completed in {result.duration_ms} ms</p>
      <p>Context version: {result.context_version_id}</p>
      <h3>Schema plan</h3>
      <pre><code>{result.schema_plan?.ddl}</code></pre>
      <h3>Contract</h3>
      <pre><code>{JSON.stringify(result.contract, null, 2)}</code></pre>
      <h3>Insights</h3>
      {result.insights?.map((insight) => (
        <article key={`${insight.category}:${insight.title}`}>
          <h4>{insight.title}</h4>
          <p>{insight.summary}</p>
          <small>Confidence: {insight.confidence}</small>
        </article>
      ))}
      {result.errors.length > 0 && (
        <aside>
          <h3>Non-fatal warnings</h3>
          <ul>{result.errors.map((item) => <li key={item}>{item}</li>)}</ul>
        </aside>
      )}
    </section>
  );
}
```

## 7. UI state handling

Handle the backend outcomes separately:

- HTTP `422`: invalid filename, encoding, empty input, oversized upload, or no valid event rows.
- HTTP `200` with `status="contract_blocked"`: generation or contract validation did not
  converge. Display `errors`; `contract` and `schema_plan` will be `null`.
- HTTP `200` with `status="error"`: contract generation succeeded but schema planning failed.
- HTTP `200` with `status="completed"`: show the contract and schema plan. The `errors` array may
  still contain non-fatal persistence or analytics warnings.
- Network error or cancellation: keep it separate from a compiler validation failure.

## 8. Suggested result layout

For a useful demo, render these sections:

1. Run status, `run_id`, duration, and context version.
2. Primary entity and funnel steps from `contract`.
3. Metrics and dimensions from `contract`.
4. Generated ClickHouse DDL from `schema_plan.ddl`.
5. Insights with confidence values.
6. Blocking errors or non-fatal warnings.

Render DDL and contract JSON as text. Never insert either with `dangerouslySetInnerHTML`.

## 9. Integration acceptance checklist

- The three health endpoints pass.
- The approved context was bootstrapped before the request.
- The frontend sends real `File` values under `spec` and `events`.
- The frontend does not manually set the multipart `Content-Type` header.
- The first request uses `dry_run=true`.
- Loading and cancellation work for multi-minute requests.
- HTTP errors and pipeline status errors are rendered differently.
- A completed Status Sharing run shows a `share_id`-grain funnel without requiring `user_id` on
  recipient-open events.
- Generated DDL is displayed as escaped text and is not executed by the frontend.
