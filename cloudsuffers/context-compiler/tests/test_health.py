from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm.fake import FakeStructuredGenerationProvider
from app.llm.provider import ProviderFailureCategory, ProviderHealthResult
from app.main import create_app
from app.services.health import HealthService


class StubClickHouseRepository:
    def __init__(self, result: bool = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def ping(self) -> bool:
        if self.error:
            raise self.error
        return self.result


def build_client(repository: StubClickHouseRepository) -> TestClient:
    settings = Settings(langfuse_enabled=False, _env_file=None)
    app = create_app(settings=settings, health_service=HealthService(repository))
    return TestClient(app)


def test_application_health_does_not_require_external_services() -> None:
    repository = StubClickHouseRepository(error=RuntimeError("must not be called"))

    with build_client(repository) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "context-compiler"
    assert body["langfuse"] == "disabled"
    assert body["timestamp"].endswith("Z")


def test_clickhouse_health_is_ok_when_ping_succeeds() -> None:
    with build_client(StubClickHouseRepository(result=True)) as client:
        response = client.get("/health/clickhouse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "clickhouse"
    assert body["latency_ms"] is not None
    assert body["detail"] is None


def test_clickhouse_health_is_unavailable_when_ping_returns_false() -> None:
    with build_client(StubClickHouseRepository(result=False)) as client:
        response = client.get("/health/clickhouse")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["detail"] == "ping failed"


def test_clickhouse_health_hides_connection_error_details() -> None:
    repository = StubClickHouseRepository(error=RuntimeError("secret server detail"))

    with build_client(repository) as client:
        response = client.get("/health/clickhouse")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["detail"] == "connection failed"
    assert "secret server detail" not in response.text


def test_llm_health_reports_reachable_configured_model() -> None:
    provider = FakeStructuredGenerationProvider([])
    settings = Settings(langfuse_enabled=False, _env_file=None)
    app = create_app(
        settings=settings,
        health_service=HealthService(StubClickHouseRepository()),
        structured_provider=provider,
    )

    with TestClient(app) as client:
        response = client.get("/health/llm")

    assert response.status_code == 200
    body = response.json()
    expected = {
        "status": "ok",
        "service": "llm",
        "configured": True,
        "reachable": True,
        "model": "fake-model",
        "model_available": True,
    }
    assert all(body[key] == value for key, value in expected.items())
    assert provider.requests == []


def test_llm_health_returns_structured_unavailable_response() -> None:
    provider = FakeStructuredGenerationProvider(
        [],
        health_result=ProviderHealthResult(
            status="unavailable",
            configured=True,
            reachable=False,
            model="fake-model",
            model_available=None,
            latency_ms=12,
            error_category=ProviderFailureCategory.CONNECTION_ERROR,
            detail="Could not connect to the LLM provider",
        ),
    )
    settings = Settings(langfuse_enabled=False, _env_file=None)
    app = create_app(settings=settings, structured_provider=provider)

    with TestClient(app) as client:
        response = client.get("/health/llm")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["error_category"] == "connection_error"
    assert body["detail"] == "Could not connect to the LLM provider"
    assert provider.requests == []
