from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.agents.analytics import AnalyticsAgent
from app.agents.context_agent import ContextAgent
from app.agents.instrumentation import InstrumentationAgent
from app.agents.schema_planner import SchemaPlanner
from app.api.contracts import router as contracts_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.api.pipeline import router as pipeline_router
from app.api.profiles import router as profiles_router
from app.clickhouse.client import build_clickhouse_client
from app.clickhouse.repository import ClickHouseHealthRepository
from app.context.repository import ClickHouseContextRepository, ContextRepositoryProtocol
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import configure_otel, instrument_fastapi
from app.core.tracing import configure_langfuse, shutdown_langfuse
from app.llm.provider import OpenAICompatibleProvider, StructuredGenerationProvider
from app.metrics.baseline import BaselineMetricsService
from app.profiling.profiler import ProfilerOptions, SourceProfiler
from app.services.health import HealthService


def create_app(
    settings: Settings | None = None,
    health_service: HealthService | None = None,
    source_profiler: SourceProfiler | None = None,
    structured_provider: StructuredGenerationProvider | None = None,
    context_repository: ContextRepositoryProtocol | None = None,
    schema_planner: SchemaPlanner | None = None,
    context_agent: ContextAgent | None = None,
    analytics_agent: AnalyticsAgent | None = None,
    baseline_metrics_service: BaselineMetricsService | None = None,
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
    provider = structured_provider or OpenAICompatibleProvider(app_settings)
    approved_context_repository = context_repository or ClickHouseContextRepository(
        lambda: build_clickhouse_client(app_settings),
        app_settings.clickhouse_metadata_database,
    )
    instrumentation_agent = InstrumentationAgent(
        provider,
        context_max_chars=app_settings.contract_context_max_chars,
        total_timeout_seconds=app_settings.llm_total_generation_timeout_seconds,
    )
    schema_planner_instance = schema_planner or SchemaPlanner(
        client_factory=lambda: build_clickhouse_client(app_settings),
        database=app_settings.clickhouse_database,
    )
    context_agent_instance = context_agent or ContextAgent(
        context_repository=approved_context_repository,
        client_factory=lambda: build_clickhouse_client(app_settings),
        metadata_database=app_settings.clickhouse_metadata_database,
    )
    analytics_agent_instance = analytics_agent or AnalyticsAgent(
        provider=provider,
        client_factory=lambda: build_clickhouse_client(app_settings),
        analytical_database=app_settings.clickhouse_database,
        metadata_database=app_settings.clickhouse_metadata_database,
    )
    baseline_metrics_instance = baseline_metrics_service or BaselineMetricsService(
        lambda: build_clickhouse_client(app_settings),
        app_settings.clickhouse_database,
        app_settings.clickhouse_metadata_database,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # Configure OpenTelemetry (traces + metrics to ClickHouse)
        trace_provider, meter_provider = configure_otel()
        application.state.otel_trace_provider = trace_provider
        application.state.otel_meter_provider = meter_provider
        
        # Configure Langfuse (AI observability)
        application.state.langfuse = configure_langfuse(app_settings)
        
        logger.info(
            "application_started",
            extra={"app_env": app_settings.app_env, "version": __version__},
        )
        try:
            yield
        finally:
            try:
                repository.close()
            except Exception:
                logger.warning("clickhouse_client_shutdown_failed")
            try:
                await provider.aclose()
            except Exception:
                logger.warning("llm_provider_shutdown_failed")
            try:
                approved_context_repository.close()
            except Exception:
                logger.warning("context_repository_shutdown_failed")
            try:
                schema_planner_instance.close()
            except Exception:
                logger.warning("schema_planner_shutdown_failed")
            try:
                context_agent_instance.close()
            except Exception:
                logger.warning("context_agent_shutdown_failed")
            try:
                analytics_agent_instance.close()
            except Exception:
                logger.warning("analytics_agent_shutdown_failed")
            try:
                baseline_metrics_instance.close()
            except Exception:
                logger.warning("baseline_metrics_shutdown_failed")
            shutdown_langfuse(application.state.langfuse)
            logger.info("application_stopped")

    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_allowed_origins,
        allow_credentials=app_settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )
    app.state.settings = app_settings
    app.state.health_service = service
    app.state.source_profiler = profiler
    app.state.instrumentation_agent = instrumentation_agent
    app.state.llm_provider = provider
    app.state.context_repository = approved_context_repository
    app.state.schema_planner = schema_planner_instance
    app.state.context_agent = context_agent_instance
    app.state.analytics_agent = analytics_agent_instance
    app.state.baseline_metrics = baseline_metrics_instance
    app.state.langfuse = None
    app.include_router(health_router)
    app.include_router(profiles_router)
    app.include_router(contracts_router)
    app.include_router(pipeline_router)
    app.include_router(dashboard_router)
    
    @app.get("/")
    def root() -> dict[str, str]:
        """Root endpoint for service verification."""
        return {
            "service": "Context Compiler",
            "status": "running",
            "version": __version__,
        }
    
    # Instrument FastAPI with OpenTelemetry (automatic request tracing)
    instrument_fastapi(app)
    
    return app


app = create_app()
