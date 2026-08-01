from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.agents.analytics import AnalyticsAgent
from app.agents.context_agent import ContextAgent
from app.agents.schema_planner import SchemaPlanner
from app.context.bootstrap import build_base_context_bundle
from app.context.repository import InMemoryContextRepository
from app.core.config import Settings
from app.llm.fake import FakeStructuredGenerationProvider
from app.main import create_app
from app.profiling.profiler import SourceProfiler
from tests.test_instrumentation_agent import SPEC, contract_data, encoded

EVENTS = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"
BASE_CONTEXT = Path(__file__).parents[1] / "docs" / "base_context.md"


class StubClient:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def command(self, statement: str) -> None:
        self.commands.append(statement)

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))

    def query(self, statement: str, parameters: dict[str, Any] | None = None):
        self.queries.append((statement, parameters or {}))

        class Result:
            result_rows: list[list[Any]] = []
            column_names: list[str] = []

        return Result()

    def close(self) -> None:
        return None


def _approved_context_repository() -> InMemoryContextRepository:
    repository = InMemoryContextRepository()
    repository.persist_bootstrap(build_base_context_bundle(BASE_CONTEXT))
    return repository


def _stub_backed_app(
    provider: FakeStructuredGenerationProvider,
    stub_client: StubClient,
    context_repository: InMemoryContextRepository | None = None,
):
    settings = Settings(langfuse_enabled=False, _env_file=None)
    repo = context_repository or _approved_context_repository()

    def factory() -> StubClient:
        return stub_client

    # Also monkey-patch the in-memory repository to expose _get_client for pipeline/dashboard.
    repo._get_client = lambda: stub_client  # type: ignore[assignment]

    return create_app(
        settings=settings,
        structured_provider=provider,
        context_repository=repo,
        schema_planner=SchemaPlanner(factory, database=settings.clickhouse_database),
        context_agent=ContextAgent(
            context_repository=repo,
            client_factory=factory,
            metadata_database=settings.clickhouse_metadata_database,
        ),
        analytics_agent=AnalyticsAgent(
            provider=provider,
            client_factory=factory,
            analytical_database=settings.clickhouse_database,
            metadata_database=settings.clickhouse_metadata_database,
        ),
    )


def test_pipeline_run_end_to_end_returns_completed_run() -> None:
    profile = SourceProfiler().profile(EVENTS)
    contract_json = encoded(contract_data(profile))
    insights_response = json.dumps(
        {
            "insights": [
                {
                    "title": "Application starts stable week over week",
                    "summary": "Volume is flat with no significant change in device mix.",
                    "confidence": 0.6,
                    "category": "trend",
                    "evidence_ids": ["weekly_trend_purchases"],
                }
            ]
        }
    )
    provider = FakeStructuredGenerationProvider([contract_json, insights_response])
    client = StubClient()
    app = _stub_backed_app(provider, client)

    with TestClient(app) as http:
        response = http.post(
            "/pipeline/run",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
            data={"dry_run": "true"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["feature_slug"] == "express_checkout"
    assert body["schema_plan"]["table_name"] == "express_checkout_events"
    # Dry-run should not deploy any DDL.
    assert body["schema_plan"]["deployed"] is False
    assert client.commands == []
    assert body["context_version_id"]
    assert isinstance(body["insights"], list)


def test_pipeline_run_reports_blocked_contract_without_deploying() -> None:
    provider = FakeStructuredGenerationProvider(["bad", "bad", "bad"])
    client = StubClient()
    app = _stub_backed_app(provider, client)

    with TestClient(app) as http:
        response = http.post(
            "/pipeline/run",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "contract_blocked"
    assert body["schema_plan"] is None
    assert body["errors"]


def test_pipeline_run_blocks_when_no_approved_context_is_present() -> None:
    provider = FakeStructuredGenerationProvider([])
    empty_repository = InMemoryContextRepository()
    stub = StubClient()
    empty_repository._get_client = lambda: stub  # type: ignore[assignment]
    settings = Settings(langfuse_enabled=False, _env_file=None)
    app = create_app(
        settings=settings,
        structured_provider=provider,
        context_repository=empty_repository,
        schema_planner=SchemaPlanner(lambda: stub, database=settings.clickhouse_database),
        context_agent=ContextAgent(
            context_repository=empty_repository,
            client_factory=lambda: stub,
            metadata_database=settings.clickhouse_metadata_database,
        ),
        analytics_agent=AnalyticsAgent(
            provider=provider,
            client_factory=lambda: stub,
            analytical_database=settings.clickhouse_database,
            metadata_database=settings.clickhouse_metadata_database,
        ),
    )

    with TestClient(app) as http:
        response = http.post(
            "/pipeline/run",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "contract_blocked"
    assert any("approved context" in item.lower() for item in body["errors"])
