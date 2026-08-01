# Context Compiler UI

This standalone sibling project contains two UI services connected to the FastAPI backend:

- React dashboard: `http://localhost:4173`
- LibreChat: `http://localhost:3080`

The React dashboard runs the complete `/pipeline/run` orchestration in dry-run mode by default and
renders the contract, schema plan, warnings, and insights. It also checks API, ClickHouse, and LLM
health independently.

## Start the dashboard for development

Keep the backend running on port 8000, then:

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. The browser calls `http://localhost:8000` directly by default. The
backend and frontend are separate processes; no Vite or Nginx API proxy is used. The backend's
CORS configuration must include the frontend origin.

## Deploy the dashboard to Vercel

Create a Vercel project with `cloudsuffers/ui` as its Root Directory. Vercel detects Vite; use
`npm run build` as the build command and `dist` as the output directory. Configure:

```dotenv
VITE_COMPILER_API_URL=https://your-context-compiler.up.railway.app
```

The value is the Railway backend origin without a trailing slash.
On Railway, add the deployed Vercel origin to
`CONTEXT_COMPILER_CORS_ALLOWED_ORIGINS`. Preview deployments need either their explicit preview
origin or a deliberate preview-domain policy; do not use credentialed wildcard CORS.

The Vercel build contains no backend credentials. Only the public backend origin is exposed to
the browser; Langfuse, ClickHouse, and model keys remain on Railway.

## Start the complete UI stack

Keep the backend running on port 8000, then create LibreChat secrets and start the UI stack:

```powershell
Copy-Item .env.example .env
# Replace every replace-with-* value in .env with a random secret.
docker compose up -d --build
```

Open the dashboard, then use **Open LibreChat** to register the first account (the first account is the administrator). Provider keys are configured as `user_provided`, so LibreChat asks each user for their own key rather than storing one in Git.

## Scope

The dashboard calls `/health`, `/health/clickhouse`, `/health/llm`, and `/pipeline/run`. LibreChat is
intentionally a separate service; it is not presented as a Context Compiler agent until an
OpenAI-compatible gateway, MCP server, or LibreChat Action exists.
