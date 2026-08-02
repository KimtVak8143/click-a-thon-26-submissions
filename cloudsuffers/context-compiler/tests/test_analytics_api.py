from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.agents.analytics import AnalyticsAgent
from app.context.bootstrap import build_base_context_bundle
from app.context.repository import InMemoryContextRepository
from app.core.config import Settings
from app.llm.fake import FakeStructuredGenerationProvider
from app.main import create_app

BASE_CONTEXT = Path(__file__).parents[1] / "docs" / "base_context.md"


class _StubResult:
    result_rows: list[list[Any]] = []
    column_names: list[str] = []


class _StubClient:
    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> _StubResult:
        return _StubResult()

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        return None

    def command(self, statement: str) -> None:
        return None

    def close(self) -> None:
        return None


def _approved_context_repository() -> InMemoryContextRepository:
    repository = InMemoryContextRepository()
    repository.persist_bootstrap(build_base_context_bundle(BASE_CONTEXT))
    return repository


def _probe_answer(answer: str) -> str:
    return json.dumps({"answer": answer, "findings": []})


def _app_with_provider(provider: FakeStructuredGenerationProvider, *, empty_context: bool = False):
    settings = Settings(langfuse_enabled=False, _env_file=None)
    stub = _StubClient()
    repository = InMemoryContextRepository() if empty_context else _approved_context_repository()
    repository._get_client = lambda: stub  # type: ignore[assignment]
    return create_app(
        settings=settings,
        structured_provider=provider,
        context_repository=repository,
        analytics_agent=AnalyticsAgent(
            provider=provider,
            client_factory=lambda: stub,
            analytical_database=settings.clickhouse_database,
            metadata_database=settings.clickhouse_metadata_database,
        ),
    )


def test_probe_endpoint_returns_answer_for_data_mode() -> None:
    provider = FakeStructuredGenerationProvider([_probe_answer("No significant issues found.")])
    app = _app_with_provider(provider)

    with TestClient(app) as http:
        response = http.post("/analytics/probe", json={"question": "Any regressions?"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "data"
    assert body["answer"] == "No significant issues found."
    assert body["findings"] == []


def test_probe_endpoint_context_audit_mode() -> None:
    answer = _probe_answer("Context looks internally consistent.")
    provider = FakeStructuredGenerationProvider([answer])
    app = _app_with_provider(provider)

    with TestClient(app) as http:
        response = http.post(
            "/analytics/probe",
            json={
                "question": "Is anything in the base context wrong, stale, or self-contradictory?",
                "mode": "context_audit",
            },
        )

    assert response.status_code == 200
    assert response.json()["mode"] == "context_audit"


def test_probe_endpoint_rejects_invalid_mode() -> None:
    provider = FakeStructuredGenerationProvider([])
    app = _app_with_provider(provider)

    with TestClient(app) as http:
        response = http.post(
            "/analytics/probe",
            json={"question": "Any regressions?", "mode": "not_a_real_mode"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_mode"


def test_probe_endpoint_requires_approved_context() -> None:
    provider = FakeStructuredGenerationProvider([])
    app = _app_with_provider(provider, empty_context=True)

    with TestClient(app) as http:
        response = http.post("/analytics/probe", json={"question": "Any regressions?"})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_approved_context"


def test_probe_endpoint_rejects_empty_question() -> None:
    provider = FakeStructuredGenerationProvider([])
    app = _app_with_provider(provider)

    with TestClient(app) as http:
        response = http.post("/analytics/probe", json={"question": ""})

    assert response.status_code == 422
