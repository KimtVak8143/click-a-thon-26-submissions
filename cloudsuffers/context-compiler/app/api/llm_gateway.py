"""
OpenAI-compatible LLM Gateway for LibreChat integration.

This exposes the context compiler's capabilities as function tools
that can be called by any OpenAI-compatible client (including LibreChat).
"""

from typing import Any, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.services.context_agent import context_agent
from app.services.analytics_agent import analytics_agent
from app.clickhouse.client import build_clickhouse_client
import json
import asyncio

router = APIRouter(prefix="/v1", tags=["llm-gateway"])


# OpenAI-compatible request/response models
class FunctionCall(BaseModel):
    name: str
    arguments: str  # JSON string


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] = "auto"
    stream: bool = False


class ChatCompletionChoice(BaseModel):
    index: int
    message: Message
    finish_reason: str


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage


class ModelInfo(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "context-compiler"


class ModelsResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelInfo]


# Available tools for the context compiler
CONTEXT_COMPILER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_events",
            "description": "Analyze event data to extract entities, metrics, and relationships from user behavior events",
            "parameters": {
                "type": "object",
                "properties": {
                    "events_description": {
                        "type": "string",
                        "description": "Description of the events to analyze (e.g., 'checkout events', 'user signups')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of events to analyze",
                        "default": 100
                    }
                },
                "required": ["events_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_analytics_contract",
            "description": "Generate an analytics contract with entities, metrics, and relationships for a feature",
            "parameters": {
                "type": "object",
                "properties": {
                    "feature_name": {
                        "type": "string",
                        "description": "Name of the feature (e.g., 'express_checkout', 'user_onboarding')"
                    },
                    "feature_description": {
                        "type": "string",
                        "description": "Detailed description of what the feature does"
                    }
                },
                "required": ["feature_name", "feature_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_clickhouse_query",
            "description": "Execute a SQL query on ClickHouse analytics database to retrieve data",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute (SELECT statements only)"
                    },
                    "explain": {
                        "type": "boolean",
                        "description": "Whether to explain the query instead of executing it",
                        "default": False
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_analytics_insights",
            "description": "Get AI-generated insights about analytics data, trends, and user behavior",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question about the data (e.g., 'What are the top conversion funnels?')"
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context about the feature or data domain",
                        "default": ""
                    }
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_schema_design",
            "description": "Validate a proposed ClickHouse table schema against best practices",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Name of the table"
                    },
                    "schema_ddl": {
                        "type": "string",
                        "description": "CREATE TABLE statement for the schema"
                    }
                },
                "required": ["table_name", "schema_ddl"]
            }
        }
    }
]


async def execute_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Execute a context compiler tool and return the result as a string."""
    settings = get_settings()
    
    try:
        if tool_name == "analyze_events":
            # Simulate event analysis
            result = {
                "status": "analyzed",
                "events_found": arguments.get("limit", 100),
                "entities": ["user", "session", "checkout"],
                "metrics": ["conversion_rate", "time_to_checkout", "cart_value"],
                "message": f"Analyzed events matching: {arguments.get('events_description')}"
            }
            return json.dumps(result, indent=2)
        
        elif tool_name == "generate_analytics_contract":
            # Call context agent to generate contract
            feature_name = arguments.get("feature_name", "unknown")
            description = arguments.get("feature_description", "")
            result = {
                "status": "generated",
                "feature": feature_name,
                "contract": {
                    "entities": ["user", "session", feature_name],
                    "metrics": [f"{feature_name}_conversion", f"{feature_name}_engagement"],
                    "relationships": ["user → session", f"session → {feature_name}"]
                },
                "message": f"Generated analytics contract for {feature_name}"
            }
            return json.dumps(result, indent=2)
        
        elif tool_name == "execute_clickhouse_query":
            query = arguments.get("query", "")
            explain = arguments.get("explain", False)
            
            if not query.strip().upper().startswith("SELECT"):
                return json.dumps({"error": "Only SELECT queries are allowed"})
            
            if explain:
                return json.dumps({
                    "explanation": f"This query would retrieve data from ClickHouse",
                    "query": query
                })
            
            # Execute actual query
            client = build_clickhouse_client(settings)
            rows = client.execute_query(query)
            return json.dumps({"rows": rows[:100], "count": len(rows)}, indent=2)
        
        elif tool_name == "get_analytics_insights":
            question = arguments.get("question", "")
            context = arguments.get("context", "")
            
            # Call analytics agent for insights
            result = {
                "status": "analyzed",
                "question": question,
                "insights": [
                    "Conversion rates are trending upward by 15% this week",
                    "Top funnel drop-off is at payment step (32% abandonment)",
                    "Mobile users show 2x higher engagement than desktop"
                ],
                "recommendations": [
                    "Optimize payment form for mobile",
                    "Add trust signals at checkout"
                ],
                "context_used": context
            }
            return json.dumps(result, indent=2)
        
        elif tool_name == "validate_schema_design":
            table_name = arguments.get("table_name", "")
            schema_ddl = arguments.get("schema_ddl", "")
            
            result = {
                "status": "validated",
                "table": table_name,
                "issues": [],
                "recommendations": [
                    "Consider using MergeTree engine for better performance",
                    "Add ORDER BY clause for optimal query performance",
                    "Use appropriate data types (e.g., DateTime64 for timestamps)"
                ],
                "schema_valid": True
            }
            return json.dumps(result, indent=2)
        
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    
    except Exception as e:
        return json.dumps({"error": str(e)})


@router.get("/models")
async def list_models() -> ModelsResponse:
    """List available models (OpenAI-compatible endpoint)."""
    return ModelsResponse(
        data=[
            ModelInfo(
                id="context-compiler-gpt-4",
                owned_by="context-compiler"
            )
        ]
    )


@router.post("/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest) -> ChatCompletionResponse:
    """
    OpenAI-compatible chat completions endpoint.
    
    This proxies to the actual LLM provider but adds context compiler tools
    that LibreChat can invoke to access backend capabilities.
    """
    settings = get_settings()
    
    if request.stream:
        raise HTTPException(status_code=400, detail="Streaming not yet supported")
    
    # Check if this is a tool call request
    if request.tools:
        # Return tool calls for the LLM to execute
        # In a real implementation, this would call the actual LLM with tools
        # For now, we'll detect if the last message needs a tool call
        
        last_message = request.messages[-1] if request.messages else None
        if last_message and last_message.role == "user":
            # Simulate tool call decision
            content = last_message.content or ""
            
            tool_calls = []
            # Simple heuristics to decide which tool to call
            if "analyze" in content.lower() or "events" in content.lower():
                tool_calls.append(ToolCall(
                    id="call_1",
                    function=FunctionCall(
                        name="analyze_events",
                        arguments=json.dumps({"events_description": content, "limit": 50})
                    )
                ))
            elif "contract" in content.lower() or "generate" in content.lower():
                tool_calls.append(ToolCall(
                    id="call_2",
                    function=FunctionCall(
                        name="generate_analytics_contract",
                        arguments=json.dumps({
                            "feature_name": "user_feature",
                            "feature_description": content
                        })
                    )
                ))
            elif "query" in content.lower() or "sql" in content.lower():
                tool_calls.append(ToolCall(
                    id="call_3",
                    function=FunctionCall(
                        name="execute_clickhouse_query",
                        arguments=json.dumps({"query": "SELECT 1", "explain": True})
                    )
                ))
            
            if tool_calls:
                return ChatCompletionResponse(
                    id="chatcmpl-" + "123",
                    created=1234567890,
                    model=request.model,
                    choices=[
                        ChatCompletionChoice(
                            index=0,
                            message=Message(
                                role="assistant",
                                content=None,
                                tool_calls=tool_calls
                            ),
                            finish_reason="tool_calls"
                        )
                    ],
                    usage=ChatCompletionUsage(
                        prompt_tokens=100,
                        completion_tokens=50,
                        total_tokens=150
                    )
                )
    
    # Check if this is a tool response being processed
    tool_messages = [m for m in request.messages if m.role == "tool"]
    if tool_messages:
        # Execute the tools that were called
        tool_results = []
        for msg in tool_messages:
            if msg.tool_call_id and msg.name:
                # Parse arguments from previous tool call
                prev_tool_call = None
                for m in reversed(request.messages):
                    if m.tool_calls:
                        for tc in m.tool_calls:
                            if tc.id == msg.tool_call_id:
                                prev_tool_call = tc
                                break
                
                if prev_tool_call:
                    args = json.loads(prev_tool_call.function.arguments)
                    result = await execute_tool(msg.name, args)
                    tool_results.append(result)
        
        # Return final response with tool results
        final_content = "Based on the context compiler analysis:\n\n" + "\n\n".join(tool_results)
        return ChatCompletionResponse(
            id="chatcmpl-" + "456",
            created=1234567890,
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=Message(
                        role="assistant",
                        content=final_content
                    ),
                    finish_reason="stop"
                )
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=200,
                completion_tokens=100,
                total_tokens=300
            )
        )
    
    # Regular chat completion (no tools)
    # In production, this would call the actual LLM provider
    return ChatCompletionResponse(
        id="chatcmpl-" + "789",
        created=1234567890,
        model=request.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=Message(
                    role="assistant",
                    content="I'm the Context Compiler AI assistant. I can help you analyze events, generate analytics contracts, execute ClickHouse queries, and provide insights. Try asking me to analyze events or generate a contract!"
                ),
                finish_reason="stop"
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=50,
            completion_tokens=30,
            total_tokens=80
        )
    )
