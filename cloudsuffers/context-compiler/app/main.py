from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.api.profiles import router as profiles_router
from app.clickhouse.client import build_clickhouse_client
from app.clickhouse.repository import ClickHouseHealthRepository
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.tracing import configure_langfuse, shutdown_langfuse
from app.profiling.profiler import ProfilerOptions, SourceProfiler
from app.services.health import HealthService


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
    source_profiler: SourceProfiler | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    logger = get_logger(__name__)

    repository = ClickHouseHealthRepository(
        client_factory=lambda: build_clickhouse_client(app_settings)
    )
    service = health_service or HealthService(repository)
    profiler = source_profiler or SourceProfiler(
        ProfilerOptions(
            example_limit=app_settings.profile_example_limit,
            distinct_limit=app_settings.profile_distinct_limit,
            example_string_length=app_settings.profile_example_string_length,
        )
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.langfuse = configure_langfuse(app_settings)
        logger.info(
            "application_started",
            extra={"app_env": app_settings.app_env, "version": __version__},
        )
        try:
            yield
        finally:
            repository.close()
            shutdown_langfuse(application.state.langfuse)
            logger.info("application_stopped")

    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.health_service = service
    app.state.source_profiler = profiler
    app.state.langfuse = None
    app.include_router(health_router)
    app.include_router(profiles_router)
    return app


app = create_app()
