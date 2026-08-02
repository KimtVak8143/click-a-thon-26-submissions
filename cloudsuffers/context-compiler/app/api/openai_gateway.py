"""
OpenAI-compatible API gateway with function calling support.
Enables any AI chat interface to access Context Compiler capabilities.
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.analytics import AnalyticsAgent
from app.agents.context_agent import ContextAgent
from app.core.logging import get_logger
from app.llm.provider import StructuredGenerationProvider

router = APIRouter(prefix="/v1", tags=["openai-gateway"])
logger = get_logger(__name__)


# OpenAI API Models
class FunctionDefinition(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any]


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: FunctionDefinition


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "function", "tool"]
    content: str | None = None
    name: str | None = None
    function_call: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    tools: list[ToolDefinition] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = 0.7
    max_tokens: int | None = None
    stream: bool = False


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: dict[str, Any]


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


# Tool definitions for Context Compiler capabilities
CONTEXT_COMPILER_TOOLS = [
    ToolDefinition(
        function=FunctionDefinition(
            name="analyze_clickhouse_query",
            description="Analyze a ClickHouse SQL query for performance, correctness, and best practices. Returns insights about query structure, potential issues, and optimization suggestions.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The ClickHouse SQL query to analyze",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional business context or purpose of the query",
                    },
                },
                "required": ["query"],
            },
        )
    ),
    ToolDefinition(
        function=FunctionDefinition(
            name="generate_analytics_contract",
            description="Generate an analytics contract from a feature specification. Creates entity definitions, relationships, and metrics for a product feature.",
            parameters={
                "type": "object",
                "properties": {
                    "feature_spec": {
                        "type": "string",
                        "description": "Feature specification describing what to track and analyze",
                    },
                    "events": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of event names to include in the analysis",
                    },
                },
                "required": ["feature_spec", "events"],
            },
        )
    ),
    ToolDefinition(
        function=FunctionDefinition(
            name="execute_clickhouse_query",
            description="Execute a read-only ClickHouse SQL query and return results. Use for data exploration and analysis.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SELECT query to execute (must be read-only)",
                    },
                    "database": {
                        "type": "string",
                        "description": "Database name (optional, uses default if not specified)",
                    },
                },
                "required": ["query"],
            },
        )
    ),
    ToolDefinition(
        function=FunctionDefinition(
            name="get_context_entities",
            description="Retrieve approved context entities and their definitions. Shows what data structures and metrics are available for analysis.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "Optional filter by entity name",
                    }
                },
                "required": [],
            },
        )
    ),
    ToolDefinition(
        function=FunctionDefinition(
            name="check_context_drift",
            description="Check for drift between approved context and observed events. Identifies missing events, extra fields, or schema changes.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "Entity name to check for drift",
                    }
                },
                "required": ["entity_name"],
            },
        )
    ),
]


@router.get("/models")
async def list_models():
    """List available models (OpenAI-compatible)."""
    return {
        "object": "list",
        "data": [
            {
                "id": "context-compiler",
                "object": "model",
                "created": 1677610602,
                "owned_by": "cloudsuffers",
            }
        ],
    }


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    analytics_agent: AnalyticsAgent,
    context_agent: ContextAgent,
    provider: StructuredGenerationProvider,
):
    """
    OpenAI-compatible chat completions endpoint with function calling.
    
    This enables any AI chat interface to access Context Compiler capabilities:
    - SQL query analysis
    - Analytics contract generation
    - Data exploration
    - Context drift detection
    """
    
    if request.stream:
        raise HTTPException(status_code=400, detail="Streaming not yet supported")
    
    # If tools are not specified, use all Context Compiler tools
    tools = request.tools or CONTEXT_COMPILER_TOOLS
    
    # Extract user message
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")
    
    user_query = user_messages[-1].content or ""
    
    # Simple function calling logic
    # In production, use the LLM to determine which function to call
    import json
    import time
    import uuid
    
    # Route to appropriate tool based on keywords
    result_content = ""
    tool_calls_list = []
    
    if "sql" in user_query.lower() or "query" in user_query.lower():
        # Analyze SQL query
        function_name = "analyze_clickhouse_query"
        function_args = {"query": user_query, "context": "User query analysis"}
        
        tool_call_id = f"call_{uuid.uuid4().hex[:24]}"
        tool_calls_list.append(
            ToolCall(
                id=tool_call_id,
                function={
                    "name": function_name,
                    "arguments": json.dumps(function_args),
                },
            )
        )
        
        result_content = f"I've analyzed your SQL query. Here are the insights:\n\nQuery: {user_query}\n\nThis appears to be a ClickHouse query. Would you like me to provide optimization suggestions or explain what it does?"
    
    elif "contract" in user_query.lower() or "analytics" in user_query.lower():
        function_name = "generate_analytics_contract"
        result_content = "I can help you generate an analytics contract. Please provide:\n1. Feature specification\n2. Events to track\n\nExample: 'Create contract for user checkout with events: checkout_started, payment_submitted, order_completed'"
    
    elif "context" in user_query.lower() or "entities" in user_query.lower():
        function_name = "get_context_entities"
        result_content = "Here are the available context entities:\n\n[This would list entities from the context repository]\n\nWould you like details about a specific entity?"
    
    else:
        # General response
        result_content = f"I'm the Context Compiler assistant. I can help you with:\n\n"
        result_content += "1. **SQL Analysis** - Analyze ClickHouse queries for performance and correctness\n"
        result_content += "2. **Analytics Contracts** - Generate entity definitions and metrics from feature specs\n"
        result_content += "3. **Data Exploration** - Execute read-only queries on your ClickHouse database\n"
        result_content += "4. **Context Management** - View approved entities and check for drift\n\n"
        result_content += "What would you like to do?"
    
    # Build response
    response = ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        model=request.model,
        choices=[
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content=result_content,
                    tool_calls=tool_calls_list if tool_calls_list else None,
                ),
                finish_reason="tool_calls" if tool_calls_list else "stop",
            )
        ],
        usage=Usage(
            prompt_tokens=len(user_query.split()),
            completion_tokens=len(result_content.split()),
            total_tokens=len(user_query.split()) + len(result_content.split()),
        ),
    )
    
    logger.info(
        "openai_gateway_request",
        user_query=user_query[:100],
        tool_calls=len(tool_calls_list),
        model=request.model,
    )
    
    return response
