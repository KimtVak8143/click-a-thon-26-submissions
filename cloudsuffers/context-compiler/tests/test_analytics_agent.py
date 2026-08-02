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
            ["week", "unique_entities"],
        ),
        "GROUP BY payment_currency": (
            [["usd", 100, 60.0], ["eur", 50, 40.0]],
            ["payment_currency", "entities", "pct"],
        ),
        "system.tables": (
            [[1]],
            ["count"],
        ),
        "countDistinctIf": (
            [[100, 20, 20.0]],
            ["step_0_count", "step_1_count", "step_0_to_step_1_pct"],
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


class _ProbeQueryResult:
    def __init__(self, rows: list[list[Any]], columns: list[str]) -> None:
        self.result_rows = rows
        self.column_names = columns


class _ProbeClient:
    """Fake ClickHouse client that answers system.tables/system.columns
    introspection from a fixed table->columns map, plus a pattern-matched
    query_map for the actual evidence queries."""

    def __init__(
        self,
        tables: dict[str, list[str]],
        query_map: dict[str, tuple[list[list[Any]], list[str]]] | None = None,
    ) -> None:
        self._tables = tables
        self._query_map = query_map or {}
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> _ProbeQueryResult:
        parameters = parameters or {}
        self.queries.append((statement, parameters))
        if "system.tables" in statement:
            return _ProbeQueryResult([[name] for name in self._tables], ["name"])
        if "system.columns" in statement:
            table = parameters.get("table")
            columns = self._tables.get(table, [])
            return _ProbeQueryResult([[name, "String"] for name in columns], ["name", "type"])
        for pattern, (rows, columns) in self._query_map.items():
            if pattern in statement:
                return _ProbeQueryResult(rows, columns)
        return _ProbeQueryResult([], [])


def _probe_answer(answer: str, findings: list[dict[str, Any]]) -> str:
    return json.dumps({"answer": answer, "findings": findings})


def test_run_probe_discovers_tables_structurally_and_answers() -> None:
    tables = {
        "express_checkout_events": [
            "id",
            "timestamp",
            "event_name",
            "application_id",
            "device_type",
        ],
        "some_materialized_view": ["date", "event_name", "unique_entity_count"],
        "otel_metrics_sum": ["ServiceName", "MetricName"],
    }
    client = _ProbeClient(tables)
    provider = FakeStructuredGenerationProvider(
        [
            _probe_answer(
                "Conversion drops sharply after the first funnel step.",
                [
                    {
                        "title": "Large drop after checkout shown",
                        "summary": "Most users never proceed past the first step.",
                        "confidence": 0.8,
                        "category": "funnel",
                        "evidence_ids": ["express_checkout_events:event_breakdown"],
                    }
                ],
            )
        ]
    )
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )
    context = _approved_context()
    assert context is not None

    result = asyncio.run(
        agent.run_probe(
            "Analyze the existing funnel and surface the most important issues, with the why.",
            context,
            uuid.uuid4(),
        )
    )

    # The materialized view (no timestamp column) and the otel table (no event
    # columns) must not be mistaken for event tables.
    assert result.tables_examined == ["express_checkout_events"]
    assert result.answer == "Conversion drops sharply after the first funnel step."
    assert len(result.findings) == 1
    assert result.findings[0].evidence_ids == [
        str(item.evidence_id)
        for item in result.query_evidence
        if item.metric_name == "express_checkout_events:event_breakdown"
    ]
    # Only device_type is present among the segment candidates.
    segment_metrics = {item.metric_name for item in result.query_evidence}
    assert "express_checkout_events:segment_device_type" in segment_metrics
    assert "express_checkout_events:segment_geoip_country_code" not in segment_metrics


def test_run_probe_prefers_context_registered_tables() -> None:
    tables = {"group_family_events": ["id", "timestamp", "event", "user_id"]}
    client = _ProbeClient(tables)
    provider = FakeStructuredGenerationProvider([_probe_answer("No major issues found.", [])])
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )
    context = _approved_context()
    assert context is not None
    context.projection["feature_tables"] = [{"table_name": "group_family_events"}]

    result = asyncio.run(
        agent.run_probe("Where are we losing conversions?", context, uuid.uuid4())
    )

    assert result.tables_examined == ["group_family_events"]


def test_run_probe_context_audit_mode_skips_clickhouse_entirely() -> None:
    client = _ProbeClient(tables={})
    provider = FakeStructuredGenerationProvider(
        [
            _probe_answer(
                "The base context declares a conflicting grain policy.",
                [
                    {
                        "title": "Conflicting grain policy",
                        "summary": "Two entities declare incompatible grains.",
                        "confidence": 0.6,
                        "category": "context",
                        "evidence_ids": [],
                    }
                ],
            )
        ]
    )
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )
    context = _approved_context()
    assert context is not None

    result = asyncio.run(
        agent.run_probe(
            "Is anything in the base context wrong, stale, or self-contradictory?",
            context,
            uuid.uuid4(),
            mode="context_audit",
        )
    )

    assert result.mode == "context_audit"
    assert result.tables_examined == []
    assert result.query_evidence == []
    assert client.queries == []  # no ClickHouse touched at all
    assert len(result.findings) == 1
    assert result.findings[0].category == "context"


def test_run_probe_returns_empty_findings_when_provider_fails() -> None:
    client = _ProbeClient(tables={})
    provider = FakeStructuredGenerationProvider(
        [ProviderError(ProviderFailureCategory.CONNECTION_ERROR, "no llm")]
    )
    agent = AnalyticsAgent(
        provider=provider,
        client_factory=lambda: client,
        analytical_database="clickathon1",
        metadata_database="compiler_meta",
    )
    context = _approved_context()
    assert context is not None

    result = asyncio.run(
        agent.run_probe("Any regressions?", context, uuid.uuid4(), mode="context_audit")
    )

    assert result.findings == []
    assert result.answer
