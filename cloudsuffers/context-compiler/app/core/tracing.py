from dataclasses import dataclass
from typing import Literal

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
    except Exception:
        logger.exception("langfuse_initialization_failed")
        return LangfuseState(status="degraded")

    logger.info("langfuse_configured", extra={"base_url": settings.langfuse_base_url})
    return LangfuseState(status="configured", client=client)


def shutdown_langfuse(state: LangfuseState | None) -> None:
    if state and state.client:
        try:
            state.client.shutdown()
        except Exception:
            logger.exception("langfuse_shutdown_failed")
