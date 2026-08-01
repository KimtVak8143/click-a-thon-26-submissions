from fastapi import Request

from app.profiling.profiler import SourceProfiler
from app.services.health import HealthService


def get_health_service(request: Request) -> HealthService:
    return request.app.state.health_service


def get_source_profiler(request: Request) -> SourceProfiler:
    return request.app.state.source_profiler
