from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langfuse import Langfuse

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
        metadata: dict[str, Any],
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
    ) -> None: ...


class InstrumentationTracer(Protocol):
    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: str,
        metadata: dict[str, Any],
        model: str | None = None,
    ) -> Iterator[TraceObservation]: ...


class _NullObservation:
    def update(
        self,
        *,
        metadata: dict[str, Any],
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
    ) -> None:
        return None


class NullInstrumentationTracer:
    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: str,
        metadata: dict[str, Any],
        model: str | None = None,
    ) -> Iterator[TraceObservation]:
        yield _NullObservation()


class SafeLangfuseInstrumentationTracer:
    """Langfuse adapter that never records prompts/candidates and never breaks generation."""

    def __init__(self, client: Langfuse | None, trace_id: str) -> None:
        self._client = client
        self._trace_id = trace_id
        self._depth = 0

    @contextmanager
    def observe(
        self,
        name: str,
        *,
        as_type: str,
        metadata: dict[str, Any],
        model: str | None = None,
    ) -> Iterator[TraceObservation]:
        if self._client is None:
            yield _NullObservation()
            return

        context_manager = None
        observation = None
        try:
            arguments: dict[str, Any] = {
                "name": name,
                "as_type": as_type,
                "metadata": metadata,
            }
            if model is not None:
                arguments["model"] = model
            if self._depth == 0:
                arguments["trace_context"] = {"trace_id": self._trace_id}
            context_manager = self._client.start_as_current_observation(**arguments)
            observation = context_manager.__enter__()
            self._depth += 1
        except Exception:
            logger.warning("langfuse_observation_start_failed", extra={"observation": name})
            yield _NullObservation()
            return

        try:
            yield _SafeObservation(observation, name)
        except BaseException as exc:
            try:
                context_manager.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                logger.warning("langfuse_observation_end_failed", extra={"observation": name})
            raise
        else:
            try:
                context_manager.__exit__(None, None, None)
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
        metadata: dict[str, Any],
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
    ) -> None:
        try:
            arguments: dict[str, Any] = {"metadata": metadata}
            if model is not None:
                arguments["model"] = model
            if usage_details:
                arguments["usage_details"] = usage_details
            self._observation.update(**arguments)
        except Exception:
            logger.warning("langfuse_observation_update_failed", extra={"observation": self._name})
