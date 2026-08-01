from fastapi import Request

from app.services.health import HealthService


def get_health_service(request: Request) -> HealthService:
    return request.app.state.health_service
