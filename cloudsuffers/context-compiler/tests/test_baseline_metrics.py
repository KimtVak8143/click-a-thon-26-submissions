from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.metrics.baseline import ATLYS_SOURCE_TABLES, BaselineMetricsService


class QueryResult:
    def __init__(self, rows: list[list[Any]], columns: list[str]) -> None:
        self.result_rows = rows
        self.column_names = columns


class RecordingClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> QueryResult:
        self.queries.append((statement, parameters or {}))
        if "FROM system.parts" in statement:
            rows = [
                [table, index + 10, (index + 1) * 100, datetime(2026, 8, 1, tzinfo=UTC)]
                for index, table in enumerate(ATLYS_SOURCE_TABLES)
            ]
            return QueryResult(rows, ["table", "rows", "first_event_time", "last_event_time"])
        if "baseline_metric_snapshots" in statement:
            return QueryResult([], [])
        return QueryResult([[10, 5, 50.0]], ["population", "converted", "rate_pct"])

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))

    def close(self) -> None:
        return None


def test_precompute_persists_versioned_aggregate_evidence() -> None:
    client = RecordingClient()
    service = BaselineMetricsService(lambda: client, "clickathon1", "compiler_meta")

    snapshot = service.precompute()

    assert len(snapshot.metrics) == 6
    assert len(snapshot.evidence_ids) == 6
    assert all(value.startswith("baseline:") for value in snapshot.evidence_ids)
    assert client.inserts[0][0] == "`compiler_meta`.`baseline_metric_snapshots`"
    compact = snapshot.compact()
    assert compact["source_database"] == "clickathon1"
    assert compact["usage_policy"]["aggregate_evidence_only"] is True
    assert all("sql" not in metric for metric in compact["metrics"])
    assert all("result_sha256" in metric for metric in compact["metrics"])


def test_baseline_service_rejects_unsafe_database_names() -> None:
    with pytest.raises(ValueError):
        BaselineMetricsService(lambda: RecordingClient(), "clickathon1; DROP", "compiler_meta")
