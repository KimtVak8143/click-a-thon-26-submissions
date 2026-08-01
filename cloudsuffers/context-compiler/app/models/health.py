from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ApplicationHealth(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "context-compiler"
    timestamp: datetime
    langfuse: Literal["disabled", "configured", "degraded", "not_initialized"]

    @classmethod
    def create(
        cls,
        *,
        langfuse: Literal["disabled", "configured", "degraded", "not_initialized"],
    ) -> "ApplicationHealth":
        return cls(timestamp=datetime.now(UTC), langfuse=langfuse)


class ClickHouseHealth(BaseModel):
    status: Literal["ok", "unavailable"]
    service: Literal["clickhouse"] = "clickhouse"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float | None = None
    detail: str | None = None
