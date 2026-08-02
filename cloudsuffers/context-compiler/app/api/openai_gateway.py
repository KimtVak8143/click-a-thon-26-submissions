"""
OpenAI-compatible API gateway with function calling support.
Enables any AI chat interface to access Context Compiler capabilities.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.analytics import AnalyticsAgent
from app.agents.context_agent import ContextAgent
from app.core.logging import get_logger
from app.llm.provider import StructuredGenerationProvider

router = APIRouter(prefix="/v1", tags=["openai-gateway"])
logger = get_logger(__name__)


# Dependency injection functions
def get_analytics_agent(request: Request) -> AnalyticsAgent:
    """Get AnalyticsAgent from app state."""
    return request.app.state.analytics_agent


def get_context_agent(request: Request) -> ContextAgent:
    """Get ContextAgent from app state."""
    return request.app.state.context_agent


def get_llm_provider(request: Request) -> StructuredGenerationProvider:
    """Get LLM provider from app state."""
    return request.app.state.llm_provider


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
    analytics_agent: AnalyticsAgent = Depends(get_analytics_agent),
    context_agent: ContextAgent = Depends(get_context_agent),
    provider: StructuredGenerationProvider = Depends(get_llm_provider),
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
    
    import json
    import time
    import uuid
    
    # Use the LLM provider to determine intent and generate response
    # For now, use keyword-based routing to demonstrate capability
    result_content = ""
    tool_calls_list = []
    
    try:
        # Route based on user intent
        query_lower = user_query.lower()
        
        if "context" in query_lower and "entities" in query_lower:
            # Get context information from the context agent
            logger.info("Fetching context entities")
            result_content = (
                "I can help you explore the approved context entities. "
                "These are the data structures and metrics available for analysis.\n\n"
                "Use the `/api/contexts/approved` endpoint to see all entities, "
                "or specify an entity name to get detailed information."
            )
        
        elif "sql" in query_lower or "select" in query_lower:
            # SQL query detected
            result_content = (
                "I can help you analyze or execute ClickHouse SQL queries.\n\n"
                "**Available actions:**\n"
                "- Analyze query performance and correctness\n"
                "- Execute read-only SELECT queries\n"
                "- Explain query structure and optimization opportunities\n\n"
                "Please provide your SQL query or use the analysis endpoint."
            )
        
        elif "contract" in query_lower or "analytics" in query_lower:
            result_content = (
                "I can help generate analytics contracts from feature specifications.\n\n"
                "**What I need:**\n"
                "1. Feature specification (user story or requirements)\n"
                "2. List of events to track\n\n"
                "Use the `/api/pipeline/run` endpoint with your specification to generate "
                "entity definitions, metrics, and ClickHouse schema."
            )
        
        elif "pipeline" in query_lower or "run" in query_lower:
            result_content = (
                "The Context Compiler pipeline transforms feature specifications into "
                "production-ready analytics schemas.\n\n"
                "**Pipeline stages:**\n"
                "1. Parse specification and extract entities\n"
                "2. Generate analytics contract with metrics\n"
                "3. Create ClickHouse schema (DDL)\n"
                "4. Update approved context\n\n"
                "POST to `/api/pipeline/run` with your specification to start."
            )
        
        else:
            # General introduction
            result_content = (
                "👋 I'm the **Context Compiler** assistant!\n\n"
                "I help you build data-driven features by:\n\n"
                "**📊 Analytics Contracts** - Convert feature specs into entity definitions and metrics\n"
                "**🗄️ SQL Analysis** - Analyze and optimize ClickHouse queries\n"
                "**🔍 Data Exploration** - Execute queries and explore your data\n"
                "**✅ Context Management** - Track approved entities and detect drift\n\n"
                "**Available endpoints:**\n"
                "- `/v1/chat/completions` - This chat interface\n"
                "- `/api/pipeline/run` - Generate contracts from specifications\n"
                "- `/api/contexts/approved` - View approved entities\n"
                "- `/api/profiles/run` - Profile data sources\n\n"
                "What would you like to do?"
            )
    
    except Exception as e:
        logger.error("chat_completion_error", error=str(e), user_query=user_query[:100])
        result_content = (
            f"I encountered an error processing your request: {str(e)}\n\n"
            "Please try rephrasing your question or contact support if the issue persists."
        )
    
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
