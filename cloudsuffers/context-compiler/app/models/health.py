from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ApplicationHealth(BaseModel):
    status: Literal["healthy"] = "healthy"
    service: str = "context-compiler"
    environment: str = "production"
    version: str = "0.1.0"
    timestamp: datetime
    langfuse: Literal["disabled", "configured", "degraded", "not_initialized"]

    @classmethod
    def create(
        cls,
        *,
        langfuse: Literal["disabled", "configured", "degraded", "not_initialized"],
        environment: str = "production",
        version: str = "0.1.0",
    ) -> "ApplicationHealth":
        return cls(
            timestamp=datetime.now(UTC),
            langfuse=langfuse,
            environment=environment,
            version=version,
        )


class ClickHouseHealth(BaseModel):
    status: Literal["ok", "unavailable"]
    service: Literal["clickhouse"] = "clickhouse"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float | None = None
    detail: str | None = None


class LLMHealth(BaseModel):
    status: Literal["ok", "unavailable"]
    service: Literal["llm"] = "llm"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    configured: bool
    reachable: bool
    model: str
    model_available: bool | None = None
    latency_ms: int = Field(ge=0)
    error_category: str | None = None
    status_code: int | None = None
    detail: str | None = None
