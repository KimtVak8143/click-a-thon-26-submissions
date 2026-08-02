#!/usr/bin/env bash

set -euo pipefail

echo "🚂 Railway startup script for Context Compiler Backend"

# Run migrations
echo "📊 Running ClickHouse migrations..."
uv run python -m app.clickhouse.migrations

# Bootstrap base context if not already present
echo "📚 Bootstrapping base context..."
uv run python -m app.cli bootstrap-context --source docs/base_context.md || echo "⚠️  Context bootstrap skipped (may already exist)"

# Start MCP server in background if enabled
if [ "${CONTEXT_COMPILER_MCP_ENABLED:-true}" = "true" ]; then
    echo "🔌 Starting MCP server on port ${CONTEXT_COMPILER_MCP_PORT:-8002}..."
    CONTEXT_COMPILER_BACKEND_URL="http://127.0.0.1:${PORT:-8000}" \
    CONTEXT_COMPILER_MCP_BIND_HOST="0.0.0.0" \
    CONTEXT_COMPILER_MCP_BIND_PORT="${CONTEXT_COMPILER_MCP_PORT:-8002}" \
    uv run python -m app.mcp_server &
    MCP_PID=$!
    echo "✅ MCP server started (PID: $MCP_PID)"
    
    # Wait a moment for MCP server to bind
    sleep 2
fi

# Start the FastAPI server (foreground)
echo "🚀 Starting FastAPI server on port ${PORT:-8000}..."
exec uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
