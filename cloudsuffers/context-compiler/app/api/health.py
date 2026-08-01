from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app import __version__
from app.api.dependencies import get_health_service, get_llm_provider
from app.llm.provider import StructuredGenerationProvider
from app.models.health import ApplicationHealth, ClickHouseHealth, LLMHealth
from app.services.health import HealthService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=ApplicationHealth)
def application_health(request: Request) -> ApplicationHealth:
    """Lightweight health check with environment info."""
    langfuse_status = getattr(request.app.state, "langfuse", None)
    settings = request.app.state.settings
    return ApplicationHealth.create(
        langfuse=langfuse_status.status if langfuse_status else "not_initialized",
        environment=settings.app_env,
        version=__version__,
    )


@router.get(
    "/health/clickhouse",
    response_model=ClickHouseHealth,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ClickHouseHealth}},
)
def clickhouse_health(
    response: Response,
    service: Annotated[HealthService, Depends(get_health_service)],
) -> ClickHouseHealth:
    health = service.check_clickhouse()
    if health.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return health


@router.get(
    "/health/llm",
    response_model=LLMHealth,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": LLMHealth}},
)
async def llm_health(
    response: Response,
    provider: Annotated[StructuredGenerationProvider, Depends(get_llm_provider)],
) -> LLMHealth:
    result = await provider.health()
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return LLMHealth(**result.model_dump())
