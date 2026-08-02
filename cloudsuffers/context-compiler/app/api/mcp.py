"""MCP endpoint integrated into FastAPI.

Instead of running a separate MCP server on port 8002, this exposes the same
MCP tools through the main FastAPI app, making it accessible via the single
Railway HTTP domain on /mcp.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from app.core.logging import get_logger
from app.profiling.models import StrictModel

router = APIRouter(prefix="/mcp", tags=["mcp"])
logger = get_logger(__name__)


class MCPRequest(StrictModel):
    jsonrpc: str = Field(default="2.0")
    method: str
    params: dict[str, Any] | None = Field(default=None)
    id: int | str | None = None


class MCPResponse(StrictModel):
    jsonrpc: str = Field(default="2.0")
    result: dict[str, Any] | None = Field(default=None)
    error: dict[str, Any] | None = Field(default=None)
    id: int | str | None = None


class MCPErrorDetail(StrictModel):
    code: int
    message: str
    data: dict[str, Any] | None = Field(default=None)


@router.post("", response_model=MCPResponse)
async def mcp_endpoint(request: Request, body: MCPRequest) -> MCPResponse:
    """MCP protocol endpoint - handles tools/list, tools/call, and resources/list.
    
    This is the same functionality as the standalone MCP server, but integrated
    into the main FastAPI app so it's accessible via the single Railway domain.
    """
    method = body.method
    params = body.params or {}

    try:
        if method == "tools/list":
            return MCPResponse(
                id=body.id,
                result={
                    "tools": [
                        {
                            "name": "analytics_probe",
                            "description": (
                                "Ask the Analytics Agent an open-ended question, grounded in real evidence. "
                                "mode='data' (default) gathers evidence across every event-shaped table; "
                                "mode='context_audit' critiques the approved context for staleness or contradictions. "
                                "Every call produces a Langfuse trace."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "question": {
                                        "type": "string",
                                        "description": "The analytics question to answer",
                                    },
                                    "mode": {
                                        "type": "string",
                                        "enum": ["data", "context_audit"],
                                        "default": "data",
                                        "description": "Query mode: 'data' for ClickHouse queries, 'context_audit' for context layer critique",
                                    },
                                },
                                "required": ["question"],
                            },
                        },
                        {
                            "name": "latest_pipeline_runs",
                            "description": (
                                "Get the most recent pipeline runs (feature, status, when), the deployed schema "
                                "timeline, open context issues, and the context layer's before/after changelog."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                    ]
                },
            )

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name == "analytics_probe":
                # Call the analytics agent via the internal endpoint
                from app.api.analytics import run_probe
                from app.api.analytics import ProbeRequest

                probe_request = ProbeRequest(
                    question=arguments.get("question", ""),
                    mode=arguments.get("mode", "data"),
                )
                result = await run_probe(request, probe_request)
                
                return MCPResponse(
                    id=body.id,
                    result={
                        "content": [
                            {
                                "type": "text",
                                "text": result.model_dump_json(indent=2),
                            }
                        ]
                    },
                )

            elif tool_name == "latest_pipeline_runs":
                # Call the dashboard endpoint
                from app.api.dashboard import read_dashboard

                result = await read_dashboard(request)
                
                return MCPResponse(
                    id=body.id,
                    result={
                        "content": [
                            {
                                "type": "text",
                                "text": result.model_dump_json(indent=2),
                            }
                        ]
                    },
                )

            else:
                return MCPResponse(
                    id=body.id,
                    error={
                        "code": -32601,
                        "message": f"Tool not found: {tool_name}",
                    },
                )

        elif method == "resources/list":
            # No resources exposed yet, return empty list
            return MCPResponse(
                id=body.id,
                result={"resources": []},
            )

        else:
            return MCPResponse(
                id=body.id,
                error={
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            )

    except Exception as e:
        logger.exception("MCP endpoint error")
        return MCPResponse(
            id=body.id,
            error={
                "code": -32603,
                "message": f"Internal error: {str(e)}",
            },
        )
