# Context Compiler UI

This folder contains two independent UI services and does not modify the FastAPI backend:

- React dashboard: `http://localhost:4173`
- LibreChat: `http://localhost:3080`

## Start

Keep the backend running on port 8000, then create LibreChat secrets and start the UI stack:

```powershell
Copy-Item .env.example .env
# Replace every replace-with-* value in .env with a random secret.
docker compose up -d --build
```

Open the dashboard, then use **Open LibreChat** to register the first account (the first account is the administrator). Provider keys are configured as `user_provided`, so LibreChat asks each user for their own key rather than storing one in Git.

## Scope

The dashboard currently calls only backend routes that exist: `/health`, `/health/clickhouse`, and `/profiles`. Pipeline stages and insights are explicit empty states until the backend exposes run and evidence read APIs. LibreChat is intentionally a separate service; it is not presented as a Context Compiler agent until an OpenAI-compatible gateway, MCP server, or LibreChat Action exists.
