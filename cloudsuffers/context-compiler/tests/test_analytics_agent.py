from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agents.analytics import AnalyticsAgent
from app.context.bootstrap import build_base_context_bundle
from app.context.repository import InMemoryContextRepository
from app.contracts.models import AnalyticsContract
from app.llm.fake import FakeStructuredGenerationProvider
from app.llm.provider import ProviderError, ProviderFailureCategory
from app.profiling.profiler import SourceProfiler
from tests.test_contracts import contract_data as _contract_data

_ = _contract_data
BASE_CONTEXT = Path(__file__).parents[1] / "docs" / "base_context.md"


class RecordingQueryResult:
    def __init__(self, rows: list[list[Any]], columns: list[str]) -> None:
        self.result_rows = rows
        self.column_names = columns


class RecordingClient:
    def __init__(
        self,
        query_map: dict[str, tuple[list[list[Any]], list[str]]] | None = None,
        fail_queries: set[str] | None = None,
    ) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []
        self._query_map = query_map or {}
        self._fail_queries = fail_queries or set()

    def query(self, statement: str, parameters: dict[str, Any] | None = None):
        self.queries.append((statement, parameters or {}))
        for pattern, payload in self._query_map.items():
            if pattern in statement:
                if pattern in self._fail_queries:
                    raise RuntimeError("boom")
                rows, columns = payload
                return RecordingQueryResult(rows, columns)
        return RecordingQueryResult([], [])

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))

    def command(self, statement: str) -> None:
        return None

    def close(self) -> None:
        return None


def _valid_contract() -> AnalyticsContract:
    events_path = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"
    profile = SourceProfiler().profile(events_path)
    data = _contract_data.__wrapped__(profile)
    return AnalyticsContract.model_validate_with_profile(data, profile)


def _approved_context():
    repository = InMemoryContextRepository()
    repository.persist_bootstrap(build_base_context_bundle(BASE_CONTEXT))
    return repository.latest_approved()


def _insights_json(insights: list[dict[str, Any]]) -> str:
    return json.dumps({"insights": insights})


def _default_query_map() -> dict[str, tuple[list[list[Any]], list[str]]]:
    return {
        "toMonday(timestamp)": (
            [["2026-07-01", 45]],
            ["week", "unique_purchasers"],
        ),
        "GROUP BY device_type": (
            [["ios", 100, 60.0], ["android", 50, 40.0]],
            ["device_type", "users", "pct"],
        ),
        "system.tables": (
            [[1]],
            ["count"],
        ),
        "click_to_app_pct": (
            [[100, 20, 5, 20.0, 25.0]],
            [
                "card_clicks",
                "applications_started",
                "purchases",
                "click_to_app_pct",
                "app_to_purchase_pct",
            ],
        ),
    }


def test_analytics_agent_generates_insights_and_records_evidence() -> None:
    contract = _valid_contract()
    context = _approved_context()
    assert context is not None
    provider = FakeStructuredGenerationProvider(
        [
            _insights_json(
                [
                    {
                        "title": "Card click to app fell",
                        "summary": "Click-to-app dropped week over week. Investigate.",
                        "confidence": 0.7,
                        "category": "trend",
                        "evidence_ids": ["baseline_funnel"],
                    }
                ]
            )
        ]
    )
    client = RecordingClient(query_map=_default_query_map())
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )

    result = asyncio.run(agent.run(contract, context, uuid.uuid4()))

    assert result.feature_slug == "express_checkout"
    assert len(result.insights) == 1
    assert result.insights[0].category == "trend"
    assert 0.0 <= result.insights[0].confidence <= 1.0
    # Four queries scheduled.
    assert len(result.query_evidence) == 4
    metrics = {evidence.metric_name for evidence in result.query_evidence}
    assert metrics == {
        "baseline_funnel",
        "weekly_trend_purchases",
        "top_segments_device_type",
        "feature_table_ready",
    }
    # LLM was called exactly once.
    assert len(provider.requests) == 1


def test_analytics_agent_survives_query_failures() -> None:
    contract = _valid_contract()
    context = _approved_context()
    assert context is not None
    provider = FakeStructuredGenerationProvider([_insights_json([])])
    client = RecordingClient(
        query_map=_default_query_map(),
        fail_queries={"system.tables"},
    )
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )

    result = asyncio.run(agent.run(contract, context, uuid.uuid4()))

    assert result.insights == []
    assert len(result.query_evidence) == 4
    readiness = next(
        item for item in result.query_evidence if item.metric_name == "feature_table_ready"
    )
    assert readiness.result_json == "[]"


def test_analytics_agent_returns_empty_when_llm_fails() -> None:
    contract = _valid_contract()
    context = _approved_context()
    assert context is not None
    provider = FakeStructuredGenerationProvider(
        [ProviderError(ProviderFailureCategory.CONNECTION_ERROR, "no llm")]
    )
    client = RecordingClient(query_map=_default_query_map())
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )

    result = asyncio.run(agent.run(contract, context, uuid.uuid4()))

    assert result.insights == []
    assert len(result.query_evidence) == 4


def test_persist_evidence_writes_query_and_insight_rows() -> None:
    contract = _valid_contract()
    context = _approved_context()
    assert context is not None
    provider = FakeStructuredGenerationProvider(
        [
            _insights_json(
                [
                    {
                        "title": "Baseline funnel",
                        "summary": "Purchases lag application starts by a factor of four.",
                        "confidence": 0.55,
                        "category": "funnel",
                        "evidence_ids": ["baseline_funnel"],
                    }
                ]
            )
        ]
    )
    client = RecordingClient(query_map=_default_query_map())
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )

    result = asyncio.run(agent.run(contract, context, uuid.uuid4()))
    agent.persist_evidence(result, context.context_version_id)

    insert_tables = [item[0] for item in client.inserts]
    assert any(table.endswith("`query_evidence`") for table in insert_tables)
    assert any(table.endswith("`analytics_insights`") for table in insert_tables)


def test_analytics_agent_rejects_unsafe_database_names() -> None:
    provider = FakeStructuredGenerationProvider([])
    with pytest.raises(ValueError):
        AnalyticsAgent(
            provider=provider,
            client_factory=lambda: RecordingClient(),
            analytical_database="drop; --",
            metadata_database="compiler_meta",
        )
    with pytest.raises(ValueError):
        AnalyticsAgent(
            provider=provider,
            client_factory=lambda: RecordingClient(),
            analytical_database="clickathon1",
            metadata_database="drop; --",
        )
