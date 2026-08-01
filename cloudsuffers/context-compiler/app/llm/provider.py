import asyncio
import json
import re
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.timing import log_timing
from app.llm.schema import SchemaInliningError, inline_local_json_schema_references

logger = get_logger(__name__)

_SAFE_ERROR_LENGTH = 160
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|authorization|password|secret|token)\s*[:=]\s*\S+|"
    r"\b(?:sk|pk)-[A-Za-z0-9_-]{6,})"
)


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderMessage(ProviderModel):
    role: Literal["system", "user", "assistant"]
    content: str


class PromptMeasurements(ProviderModel):
    prompt_bytes: int = Field(ge=0)
    estimated_prompt_tokens: int = Field(ge=0)
    json_schema_bytes: int = Field(ge=0)
    profile_summary_bytes: int = Field(ge=0)


class StructuredGenerationRequest(ProviderModel):
    messages: list[ProviderMessage] = Field(min_length=1)
    json_schema: dict[str, Any]
    schema_name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    measurements: PromptMeasurements | None = None
    attempt_number: int = Field(default=1, ge=1)


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


class ProviderFailureCategory(StrEnum):
    CONNECTION_ERROR = "connection_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_HTTP_ERROR = "provider_http_error"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    CANCELLED = "cancelled"
    UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"


class ProviderHealthResult(ProviderModel):
    status: Literal["ok", "unavailable"]
    configured: bool
    reachable: bool
    model: str
    model_available: bool | None = None
    latency_ms: int = Field(ge=0)
    error_category: ProviderFailureCategory | None = None
    status_code: int | None = None
    detail: str | None = None


class StructuredGenerationProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...

    async def health(self) -> ProviderHealthResult: ...

    async def aclose(self) -> None: ...


class ProviderError(RuntimeError):
    """Sanitized provider failure safe for logs, traces, and API responses."""

    def __init__(
        self,
        category: ProviderFailureCategory,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message
        self.status_code = status_code
        self.error_code = error_code


class ProviderConfigurationError(ProviderError):
    def __init__(self) -> None:
        super().__init__(
            ProviderFailureCategory.CONNECTION_ERROR,
            "LLM provider is not configured",
        )


class OpenAICompatibleProvider:
    """OpenAI-compatible structured generation using one lazy pooled HTTP client."""

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
            raise ProviderConfigurationError()
        if self._client is None:
            self._client = self._client_factory(
                base_url=self._settings.llm_base_url.rstrip("/"),
                timeout=httpx.Timeout(self._settings.llm_timeout_seconds),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={
                    "Authorization": f"Bearer {self._settings.llm_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        started = time.perf_counter()
        try:
            client = self._get_client()
            response = await self._send_with_retries(
                client,
                self._payload(request),
                sensitive_texts=[message.content for message in request.messages],
            )
            result = self._parse_response(response, started)
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        except Exception:
            raise ProviderError(
                ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR,
                "LLM provider request failed unexpectedly",
            ) from None
        return result

    async def _send_with_retries(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        sensitive_texts: list[str],
    ) -> httpx.Response:
        for retry in range(self._settings.llm_max_retries + 1):
            try:
                response = await client.post("chat/completions", json=payload)
                response.raise_for_status()
                return response
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                if retry >= self._settings.llm_max_retries:
                    raise ProviderError(
                        ProviderFailureCategory.PROVIDER_TIMEOUT,
                        "LLM provider request timed out",
                    ) from None
            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code in {408, 409, 429, 500, 502, 503, 504}
                if retry >= self._settings.llm_max_retries or not retryable:
                    code, message = _sanitized_http_error(
                        exc.response,
                        secret=self._settings.llm_api_key.get_secret_value(),
                        sensitive_texts=sensitive_texts,
                    )
                    raise ProviderError(
                        ProviderFailureCategory.PROVIDER_HTTP_ERROR,
                        message,
                        status_code=exc.response.status_code,
                        error_code=code,
                    ) from None
            except httpx.TransportError:
                if retry >= self._settings.llm_max_retries:
                    raise ProviderError(
                        ProviderFailureCategory.CONNECTION_ERROR,
                        "Could not connect to the LLM provider",
                    ) from None
            await asyncio.sleep(min(0.25 * (2**retry), 2.0))
        raise ProviderError(
            ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR,
            "LLM provider retry loop ended unexpectedly",
        )

    def _payload(self, request: StructuredGenerationRequest) -> dict[str, Any]:
        if self._settings.llm_structured_output_mode == "json_schema":
            try:
                provider_schema, schema_diagnostics = inline_local_json_schema_references(
                    request.json_schema,
                    strict_object_compliance=True,
                )
            except SchemaInliningError:
                raise ProviderError(
                    ProviderFailureCategory.INVALID_PROVIDER_RESPONSE,
                    "Provider JSON schema could not be normalized",
                    error_code="invalid_json_schema",
                ) from None
            log_timing(
                logger,
                "provider_schema_normalized",
                stage="provider_schema_normalization",
                attempt_number=request.attempt_number,
                model=self.model_name,
                provider_status="completed",
                **schema_diagnostics.as_dict(),
            )
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": provider_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        return {
            "model": self._settings.llm_model,
            "messages": [message.model_dump() for message in request.messages],
            "stream": False,
            "temperature": self._settings.llm_temperature,
            "max_tokens": self._settings.llm_max_output_tokens,
            "response_format": response_format,
        }

    def _parse_response(
        self, response: httpx.Response, started: float
    ) -> StructuredGenerationResponse:
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            response_model = body.get("model")
            model = response_model if isinstance(response_model, str) else self.model_name
            usage = _parse_usage(body.get("usage"))
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
            raise ProviderError(
                ProviderFailureCategory.INVALID_PROVIDER_RESPONSE,
                "LLM provider returned an invalid response envelope",
                status_code=response.status_code,
            ) from None
        return StructuredGenerationResponse(
            content=content,
            model=model,
            latency_ms=_elapsed_ms(started),
            usage=usage,
        )

    async def health(self) -> ProviderHealthResult:
        started = time.perf_counter()
        if not self._settings.llm_configured:
            return ProviderHealthResult(
                status="unavailable",
                configured=False,
                reachable=False,
                model=self.model_name,
                latency_ms=0,
                error_category=ProviderFailureCategory.CONNECTION_ERROR,
                detail="LLM provider is not configured",
            )
        try:
            response = await self._get_client().get("models")
            if response.status_code in {404, 405}:
                return ProviderHealthResult(
                    status="ok",
                    configured=True,
                    reachable=True,
                    model=self.model_name,
                    model_available=None,
                    latency_ms=_elapsed_ms(started),
                    status_code=response.status_code,
                    detail="provider does not expose model discovery",
                )
            response.raise_for_status()
            model_ids = _model_ids(response)
            available = self.model_name in model_ids
            return ProviderHealthResult(
                status="ok" if available else "unavailable",
                configured=True,
                reachable=True,
                model=self.model_name,
                model_available=available,
                latency_ms=_elapsed_ms(started),
                status_code=response.status_code,
                detail=None if available else "configured model is not available",
            )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            error = ProviderError(
                ProviderFailureCategory.PROVIDER_TIMEOUT,
                "LLM provider health request timed out",
            )
        except httpx.HTTPStatusError as exc:
            _, message = _sanitized_http_error(
                exc.response,
                secret=self._settings.llm_api_key.get_secret_value(),
            )
            error = ProviderError(
                ProviderFailureCategory.PROVIDER_HTTP_ERROR,
                message,
                status_code=exc.response.status_code,
            )
        except httpx.TransportError:
            error = ProviderError(
                ProviderFailureCategory.CONNECTION_ERROR,
                "Could not connect to the LLM provider",
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            error = ProviderError(
                ProviderFailureCategory.INVALID_PROVIDER_RESPONSE,
                "LLM provider returned an invalid models response",
            )
        except Exception:
            error = ProviderError(
                ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR,
                "LLM provider health check failed unexpectedly",
            )
        return ProviderHealthResult(
            status="unavailable",
            configured=True,
            reachable=error.category
            not in {
                ProviderFailureCategory.CONNECTION_ERROR,
                ProviderFailureCategory.PROVIDER_TIMEOUT,
            },
            model=self.model_name,
            latency_ms=_elapsed_ms(started),
            error_category=error.category,
            status_code=error.status_code,
            detail=error.safe_message,
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


def _model_ids(response: httpx.Response) -> set[str]:
    body = response.json()
    data = body["data"]
    if not isinstance(data, list):
        raise TypeError
    return {
        item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _sanitized_http_error(
    response: httpx.Response,
    *,
    secret: str,
    sensitive_texts: list[str] | None = None,
) -> tuple[str | None, str]:
    code = None
    message = response.reason_phrase or "LLM provider returned an HTTP error"
    try:
        body = response.json()
        error = body.get("error", body) if isinstance(body, dict) else None
        if isinstance(error, dict):
            candidate_code = error.get("code")
            candidate_message = error.get("message")
            if isinstance(candidate_code, str) and _SAFE_ERROR_CODE.fullmatch(candidate_code):
                code = candidate_code
            if isinstance(candidate_message, str):
                message = candidate_message
    except (json.JSONDecodeError, ValueError):
        pass
    message = message.replace(secret, "[REDACTED]") if secret else message
    for sensitive_text in sensitive_texts or []:
        if sensitive_text:
            message = message.replace(sensitive_text, "[REDACTED]")
    if any(
        marker in message
        for marker in ("<source_data_json>", "AnalyticsContract 1.0", "Security and evidence rules")
    ):
        message = response.reason_phrase or "LLM provider returned an HTTP error"
    message = _SENSITIVE_TEXT.sub("[REDACTED]", message)
    message = " ".join(message.split())[:_SAFE_ERROR_LENGTH]
    return code, message or "LLM provider returned an HTTP error"


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
