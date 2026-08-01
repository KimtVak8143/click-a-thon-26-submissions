from time import perf_counter

from app.clickhouse.repository import ClickHouseHealthRepositoryProtocol
from app.core.logging import get_logger
from app.models.health import ClickHouseHealth

logger = get_logger(__name__)


class HealthService:
    def __init__(self, repository: ClickHouseHealthRepositoryProtocol) -> None:
        self._repository = repository

    def check_clickhouse(self) -> ClickHouseHealth:
        started_at = perf_counter()
        try:
            available = self._repository.ping()
        except Exception as exc:
            logger.warning(
                "clickhouse_health_check_failed",
                extra={"error_type": type(exc).__name__},
            )
            return ClickHouseHealth(status="unavailable", detail="connection failed")

        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        if not available:
            return ClickHouseHealth(
                status="unavailable",
                latency_ms=latency_ms,
                detail="ping failed",
            )
        return ClickHouseHealth(status="ok", latency_ms=latency_ms)
