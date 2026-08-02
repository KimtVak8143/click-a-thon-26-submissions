#!/usr/bin/env bash

set -euo pipefail

echo "🚂 Railway startup script for Context Compiler Backend"

# Run migrations
echo "📊 Running ClickHouse migrations..."
uv run python -m app.clickhouse.migrations

# Bootstrap base context if not already present
echo "📚 Bootstrapping base context..."
uv run python -m app.cli bootstrap-context --source docs/base_context.md || echo "⚠️  Context bootstrap skipped (may already exist)"

# Note: MCP endpoint is now integrated into FastAPI at /mcp
# No separate MCP server needed - everything runs on one port

# Start the FastAPI server (foreground)
echo "🚀 Starting FastAPI server (with integrated MCP endpoint) on port ${PORT:-8000}..."
exec uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
