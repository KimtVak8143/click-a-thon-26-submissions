import asyncio
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderMessage(ProviderModel):
    role: Literal["system", "user", "assistant"]
    content: str


class StructuredGenerationRequest(ProviderModel):
    messages: list[ProviderMessage] = Field(min_length=1)
    json_schema: dict[str, Any]
    schema_name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")


class TokenUsage(ProviderModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    def as_langfuse(self) -> dict[str, int]:
        usage = {}
        if self.input_tokens is not None:
            usage["input"] = self.input_tokens
        if self.output_tokens is not None:
            usage["output"] = self.output_tokens
        if self.total_tokens is not None:
            usage["total"] = self.total_tokens
        return usage


class StructuredGenerationResponse(ProviderModel):
    content: str
    model: str
    latency_ms: int = Field(ge=0)
    usage: TokenUsage | None = None


class StructuredGenerationProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...

    async def aclose(self) -> None: ...


class ProviderError(RuntimeError):
    """A safe provider failure that contains no response body or credentials."""


class ProviderConfigurationError(ProviderError):
    pass


class OpenAICompatibleProvider:
    """OpenAI-compatible structured generation with a lazily created HTTP client."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None

    @property
    def model_name(self) -> str:
        return self._settings.llm_model or "unconfigured"

    @property
    def initialized(self) -> bool:
        return self._client is not None

    def _get_client(self) -> httpx.AsyncClient:
        if not self._settings.llm_configured:
            raise ProviderConfigurationError("LLM provider is not configured")
        if self._client is None:
            self._client = self._client_factory(
                base_url=self._settings.llm_base_url.rstrip("/"),
                timeout=self._settings.llm_timeout_seconds,
                headers={
                    "Authorization": (f"Bearer {self._settings.llm_api_key.get_secret_value()}"),
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        client = self._get_client()
        payload = {
            "model": self._settings.llm_model,
            "messages": [message.model_dump() for message in request.messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": request.json_schema,
                },
            },
        }

        started = time.perf_counter()
        response: httpx.Response | None = None
        for attempt in range(self._settings.llm_max_retries + 1):
            try:
                response = await client.post("chat/completions", json=payload)
                response.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(
                    exc, httpx.HTTPStatusError
                ) or exc.response.status_code in {
                    408,
                    409,
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if attempt >= self._settings.llm_max_retries or not retryable:
                    logger.warning(
                        "llm_generation_failed",
                        extra={"attempt": attempt + 1, "error_type": type(exc).__name__},
                    )
                    raise ProviderError("LLM generation request failed") from None
                logger.warning(
                    "llm_generation_retry",
                    extra={"attempt": attempt + 1, "error_type": type(exc).__name__},
                )
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))

        if response is None:
            raise ProviderError("LLM generation request failed")

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            response_model = body.get("model")
            model = response_model if isinstance(response_model, str) else self.model_name
            usage = _parse_usage(body.get("usage"))
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("LLM provider returned an invalid response envelope") from None

        return StructuredGenerationResponse(
            content=content,
            model=model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=usage,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _parse_usage(value: Any) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None
    prompt_tokens = value.get("prompt_tokens")
    completion_tokens = value.get("completion_tokens")
    total_tokens = value.get("total_tokens")
    if not any(isinstance(item, int) for item in (prompt_tokens, completion_tokens, total_tokens)):
        return None
    return TokenUsage(
        input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
        output_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
        total_tokens=total_tokens if isinstance(total_tokens, int) else None,
    )
