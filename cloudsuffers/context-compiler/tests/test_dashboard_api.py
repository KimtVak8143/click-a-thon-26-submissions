from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.context.repository import InMemoryContextRepository
from app.core.config import Settings
from app.llm.fake import FakeStructuredGenerationProvider
from app.main import create_app


class DashboardStubClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None):
        self.queries.append((statement, parameters or {}))

        class Result:
            def __init__(self, rows: list[list[Any]]) -> None:
                self.result_rows = rows
                self.column_names: list[str] = []

        if "schema_versions" in statement:
            return Result(
                [
                    [
                        "express_checkout",
                        1,
                        json.dumps(
                            {
                                "strategy": "materialized_aggregate",
                                "database": "atlys_analytics",
                                "tables": ["express_checkout_events"],
                            }
                        ),
                        None,
                        None,
                    ]
                ]
            )
        if "pipeline_runs" in statement:
            return Result([[uuid.uuid4(), "express_checkout", "completed", None]])
        if "context_issues" in statement:
            return Result([["CTX-002", "metric_ambiguity", "warning", "Metric ambiguity"]])
        if "context_changelog" in statement:
            return Result([["schema_added", "Added express_checkout_events", None]])
        if "ai_recommendations" in statement and "countIf" in statement:
            return Result([[4, 3, 0.82, 1]])
        if "ai_evaluations" in statement:
            return Result([["groundedness", 0.91, 0.75, 4]])
        if "ai_recommendations" in statement:
            return Result(
                [["rec-1", "trace-1", "APPROVED", "Test checkout flow", 0.82, "gpt", "7", None]]
            )
        if "ai_traces" in statement and "avgOrNull" in statement:
            return Result([[5, 120.0, 0.012, 900, 1]])
        if "ai_traces" in statement:
            return Result([["trace-1", "recommendation-lifecycle", None, 120.0, 0.002, 180, 0]])
        return Result([])

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        return None

    def command(self, statement: str) -> None:
        return None

    def close(self) -> None:
        return None


def test_dashboard_returns_populated_response() -> None:
    settings = Settings(langfuse_enabled=False, _env_file=None)
    provider = FakeStructuredGenerationProvider([])
    repository = InMemoryContextRepository()
    stub = DashboardStubClient()
    repository._get_client = lambda: stub  # type: ignore[assignment]
    app = create_app(
        settings=settings,
        structured_provider=provider,
        context_repository=repository,
    )

    with TestClient(app) as http:
        response = http.get("/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_timeline"][0]["table_name"] == "express_checkout_events"
    assert body["schema_timeline"][0]["strategy"] == "materialized_aggregate"
    assert body["pipeline_runs"][0]["feature_slug"] == "express_checkout"
    assert body["context_issues"][0]["issue_code"] == "CTX-002"
    assert body["context_changelog"][0]["change_type"] == "schema_added"
    assert body["observability"]["recommendation_count"] == 4
    assert body["observability"]["approval_rate"] == 0.75
    assert body["evaluator_scores"][0]["name"] == "groundedness"
    assert body["recommendations"][0]["recommendation_id"] == "rec-1"
    assert body["recent_traces"][0]["trace_id"] == "trace-1"


def test_dashboard_returns_empty_lists_on_clickhouse_failure() -> None:
    settings = Settings(langfuse_enabled=False, _env_file=None)
    provider = FakeStructuredGenerationProvider([])
    repository = InMemoryContextRepository()

    class BrokenClient:
        def query(self, statement: str, parameters=None):
            raise RuntimeError("no clickhouse")

        def close(self) -> None:
            return None

    repository._get_client = lambda: BrokenClient()  # type: ignore[assignment]
    app = create_app(
        settings=settings,
        structured_provider=provider,
        context_repository=repository,
    )

    with TestClient(app) as http:
        response = http.get("/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_timeline"] == []
    assert body["pipeline_runs"] == []
    assert body["context_issues"] == []
    assert body["context_changelog"] == []
    assert body["observability"]["source"] == "Unavailable"
    assert body["evaluator_scores"] == []
    assert body["recent_traces"] == []
