"""MCP server exposing the Context Compiler's own agents, not raw ClickHouse access.

Where mcp-clickhouse (see ui/compose.yaml) gives a chat client generic, ad-hoc SQL
tools with no idea what a "feature", "contract", or "context version" is, this
server wraps the backend's own already-traced HTTP API so a chat client answers
using the Analytics Agent grounded in the current context layer and the most
recent pipeline run — the same thing `/analytics/probe` and `/dashboard` already
do, just reachable as MCP tools.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastmcp import FastMCP

BACKEND_URL = os.getenv("CONTEXT_COMPILER_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
BIND_HOST = os.getenv("CONTEXT_COMPILER_MCP_BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.getenv("CONTEXT_COMPILER_MCP_BIND_PORT", "8002"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("CONTEXT_COMPILER_MCP_TIMEOUT_SECONDS", "120"))

mcp = FastMCP(
    name="context-compiler",
    instructions=(
        "Tools for analyzing product features THROUGH the Context Compiler's own "
        "Analytics Agent and context layer — not raw ClickHouse access. Prefer "
        "these over ad-hoc SQL tools when asked about a feature's funnel, "
        "conversions, trends, or whether the business context is stale or "
        "self-contradictory: these tools are grounded in the same evidence and "
        "context version the compiler's own pipeline runs use, and every call "
        "produces a Langfuse trace."
    ),
)


async def _get(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{BACKEND_URL}{path}")
        response.raise_for_status()
        return response.json()


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{BACKEND_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


@mcp.tool
async def analytics_probe(question: str, mode: str = "data") -> dict[str, Any]:
    """Ask the Analytics Agent an open-ended question, grounded in real evidence.

    mode="data" (default) gathers evidence across every event-shaped table the
    context layer currently knows about (plus anything else it can structurally
    find) and answers from that — funnels, conversion loss by segment, trends,
    regressions.

    mode="context_audit" skips ClickHouse entirely and asks the model to critique
    the approved context's own declared content — use this for "is anything in
    the base context wrong, stale, or self-contradictory?"-style questions.

    Every call produces its own Langfuse trace tagged analytics-agent, probe.
    """
    return await _post("/analytics/probe", {"question": question, "mode": mode})


@mcp.tool
async def latest_pipeline_runs() -> dict[str, Any]:
    """The most recent pipeline runs (feature, status, when), the deployed schema
    timeline, open context issues, and the context layer's before/after changelog —
    i.e. what the Context Compiler's own pipeline has actually produced most
    recently, so answers can reference it instead of stale or invented state.
    """
    return await _get("/dashboard")


def main() -> None:
    mcp.run(transport="http", host=BIND_HOST, port=BIND_PORT)


if __name__ == "__main__":
    main()
