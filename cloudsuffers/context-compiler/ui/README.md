# Context Compiler UI

This folder contains two UI services connected to the FastAPI backend:

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

Open `http://localhost:5173`. Vite proxies `/compiler-api` to the backend, so no browser CORS
configuration is needed for local development.

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
