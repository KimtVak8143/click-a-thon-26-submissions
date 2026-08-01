from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langfuse import Langfuse, propagate_attributes
from opentelemetry.context import Context, attach, detach

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class LangfuseState:
    status: Literal["disabled", "configured", "degraded"]
    client: Langfuse | None = None


def configure_langfuse(settings: Settings) -> LangfuseState:
    if not settings.langfuse_configured:
        logger.info("langfuse_disabled")
        return LangfuseState(status="disabled")

    try:
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
            environment=settings.app_env,
            mask=_langfuse_mask,
        )
    except Exception as exc:
        logger.warning("langfuse_initialization_failed", extra={"error_type": type(exc).__name__})
        return LangfuseState(status="degraded")

    logger.info("langfuse_configured", extra={"base_url": settings.langfuse_base_url})
    return LangfuseState(status="configured", client=client)


def shutdown_langfuse(state: LangfuseState | None) -> None:
    if state and state.client:
        try:
            state.client.shutdown()
        except Exception as exc:
            logger.warning("langfuse_shutdown_failed", extra={"error_type": type(exc).__name__})


class TraceObservation(Protocol):
    def update(
        self,
        *,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None: ...


class InstrumentationTracer(Protocol):
    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: str,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[TraceObservation]: ...


class _NullObservation:
    def update(
        self,
        *,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        return None


class NullInstrumentationTracer:
    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: str,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[TraceObservation]:
        yield _NullObservation()


class SafeLangfuseInstrumentationTracer:
    """Failure-isolated Langfuse adapter for nested application observations."""

    def __init__(
        self,
        client: Langfuse | None,
        trace_id: str,
        *,
        feature_name: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self._client = client
        self._trace_id = trace_id
        self._depth = 0
        self._feature_name = feature_name
        self._base_tags = tags or []

    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: str,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[TraceObservation]:
        if self._client is None:
            yield _NullObservation()
            return

        context_stack = ExitStack()
        observation = None
        try:
            if self._depth == 0:
                context_token = attach(Context())
                context_stack.callback(detach, context_token)

            all_tags = list(self._base_tags)
            if self._feature_name:
                all_tags.append(f"feature:{self._feature_name}")
            if tags:
                all_tags.extend(tags)
            all_tags = list(dict.fromkeys(all_tags))

            arguments: dict[str, Any] = {
                "name": name,
                "as_type": as_type,
            }

            if input is not None:
                arguments["input"] = _mask_sensitive_data(input)

            if metadata:
                arguments["metadata"] = _mask_sensitive_data(metadata)

            if model is not None:
                arguments["model"] = model

            if self._depth == 0:
                arguments["trace_context"] = {"trace_id": self._trace_id}

            observation = context_stack.enter_context(
                self._client.start_as_current_observation(**arguments)
            )
            if all_tags:
                context_stack.enter_context(
                    propagate_attributes(
                        tags=all_tags,
                        trace_name=name if self._depth == 0 else None,
                    )
                )
            self._depth += 1
        except Exception as exc:
            context_stack.close()
            logger.warning(
                "langfuse_observation_start_failed",
                extra={"observation": name, "error_type": type(exc).__name__},
            )
            yield _NullObservation()
            return

        try:
            yield _SafeObservation(observation, name)
        except BaseException as exc:
            try:
                observation.update(
                    level="ERROR",
                    status_message=type(exc).__name__,
                )
                context_stack.close()
            except Exception:
                logger.warning("langfuse_observation_end_failed", extra={"observation": name})
            raise
        else:
            try:
                context_stack.close()
            except Exception:
                logger.warning("langfuse_observation_end_failed", extra={"observation": name})
        finally:
            self._depth -= 1


class _SafeObservation:
    def __init__(self, observation: Any, name: str) -> None:
        self._observation = observation
        self._name = name

    def update(
        self,
        *,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        try:
            arguments: dict[str, Any] = {}

            if input is not None:
                arguments["input"] = _mask_sensitive_data(input)

            if output is not None:
                arguments["output"] = _mask_sensitive_data(output)

            if metadata:
                arguments["metadata"] = _mask_sensitive_data(metadata)

            if model is not None:
                arguments["model"] = model

            if usage_details:
                arguments["usage_details"] = _normalize_usage_details(usage_details)

            if level is not None:
                arguments["level"] = level

            if status_message is not None:
                arguments["status_message"] = status_message

            if arguments:
                self._observation.update(**arguments)
        except Exception as exc:
            logger.warning(
                "langfuse_observation_update_failed",
                extra={"observation": self._name, "error_type": type(exc).__name__},
            )


def _mask_sensitive_data(data: Any) -> Any:
    """Mask sensitive data in inputs/outputs before sending to Langfuse."""
    if isinstance(data, dict):
        masked = {}
        for key, value in data.items():
            key_lower = key.lower()
            if any(
                sensitive in key_lower
                for sensitive in ("password", "secret", "token", "api_key", "authorization")
            ):
                masked[key] = "***MASKED***"
            else:
                masked[key] = _mask_sensitive_data(value)
        return masked
    elif isinstance(data, list):
        return [_mask_sensitive_data(item) for item in data]
    elif isinstance(data, str) and len(data) > 10000:
        # Truncate very long strings to prevent token bloat
        return data[:10000] + "... (truncated)"
    else:
        return data


def _langfuse_mask(*, data: Any, **_: Any) -> Any:
    """Apply the same redaction to every payload exported by the SDK."""
    return _mask_sensitive_data(data)


def _normalize_usage_details(usage: dict[str, int]) -> dict[str, int]:
    """Normalize provider counters to the Langfuse v4 usage-details schema."""
    normalized = {
        "input": usage.get("input", usage.get("input_tokens", 0)),
        "output": usage.get("output", usage.get("output_tokens", 0)),
    }
    total = usage.get("total", usage.get("total_tokens"))
    if total is not None:
        normalized["total"] = total
    return normalized
