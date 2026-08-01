from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agents.schema_planner import SchemaPlanner, SchemaVersionRecord
from app.contracts.models import AnalyticsContract
from app.profiling.profiler import SourceProfiler
from tests.test_contracts import contract_data as _contract_data
from tests.test_contracts import source_profile as _source_profile

# Silence unused-import warnings; pytest discovers these via fixture reuse.
_ = (_contract_data, _source_profile)


class RecordingClient:
    def __init__(self, query_rows: list[list[Any]] | None = None) -> None:
        self.commands: list[str] = []
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self._rows = query_rows or []

    def command(self, statement: str) -> None:
        self.commands.append(statement)

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))

    def query(self, statement: str, parameters: dict[str, Any] | None = None):
        self.queries.append((statement, parameters or {}))

        class Result:
            def __init__(self, rows: list[list[Any]]) -> None:
                self.result_rows = rows
                self.column_names: list[str] = []

        return Result(self._rows)

    def close(self) -> None:
        pass


def _valid_contract() -> AnalyticsContract:
    events_path = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"
    profile = SourceProfiler().profile(events_path)
    data = _contract_data.__wrapped__(profile)  # unwrap the pytest fixture
    return AnalyticsContract.model_validate_with_profile(data, profile)


def test_plan_produces_event_table_ddl_and_materialized_view() -> None:
    contract = _valid_contract()
    client = RecordingClient()
    planner = SchemaPlanner(lambda: client, database="atlys_analytics")

    record = planner.plan(contract, uuid.uuid4())

    assert record.strategy_name == "materialized_aggregate"
    assert record.table_name == "express_checkout_events"
    assert record.database_name == "atlys_analytics"
    assert record.deployed_at is not None
    assert "CREATE TABLE IF NOT EXISTS" in record.ddl
    assert "ORDER BY (application_id, event_name, timestamp)" in record.ddl
    assert "CREATE MATERIALIZED VIEW" in record.ddl
    assert "toYYYYMM(timestamp)" in record.ddl
    inventory = json.loads(record.object_inventory_json)
    assert inventory["strategy"] == "materialized_aggregate"
    assert "express_checkout_events" in inventory["tables"]
    assert any("funnel_daily_mv" in table for table in inventory["tables"])
    assert len(client.commands) == 2


def test_plan_dry_run_does_not_execute_ddl() -> None:
    contract = _valid_contract()
    client = RecordingClient()
    planner = SchemaPlanner(lambda: client, database="atlys_analytics")

    record = planner.plan(contract, uuid.uuid4(), dry_run=True)

    assert record.deployed_at is None
    assert client.commands == []


def test_persist_inserts_row_into_schema_versions() -> None:
    contract = _valid_contract()
    client = RecordingClient()
    planner = SchemaPlanner(lambda: client, database="atlys_analytics")

    record = planner.plan(contract, uuid.uuid4(), dry_run=True)
    planner.persist(record, metadata_database="compiler_meta")

    assert len(client.inserts) == 1
    table, rows, columns = client.inserts[0]
    assert table == "`compiler_meta`.`schema_versions`"
    assert "schema_version_id" in columns
    assert rows[0][3] == "express_checkout"


def test_planner_rejects_unsafe_database_names() -> None:
    with pytest.raises(ValueError):
        SchemaPlanner(lambda: RecordingClient(), database="drop; --")


def test_list_deployed_parses_stored_records() -> None:
    contract = _valid_contract()
    client = RecordingClient(
        query_rows=[
            [
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
                contract.feature.slug,
                1,
                "atlys_analytics",
                json.dumps(
                    {
                        "strategy": "dedicated_event_table",
                        "database": "atlys_analytics",
                        "tables": ["express_checkout_events"],
                    }
                ),
                "CREATE TABLE ...",
                None,
                None,
            ]
        ]
    )
    planner = SchemaPlanner(lambda: client, database="atlys_analytics")

    records = planner.list_deployed("compiler_meta", feature_slug=contract.feature.slug)

    assert len(records) == 1
    assert records[0].feature_slug == contract.feature.slug
    assert records[0].table_name == "express_checkout_events"
    assert isinstance(records[0], SchemaVersionRecord)


def test_planner_maps_field_definitions_to_columns() -> None:
    contract = _valid_contract()
    client = RecordingClient()
    planner = SchemaPlanner(lambda: client, database="atlys_analytics")

    record = planner.plan(contract, uuid.uuid4(), dry_run=True)

    # Fields from contract get mapped to columns using source_path snake_case.
    assert "application_id String" in record.ddl
    assert "payment_amount" in record.ddl or "payment_amount Decimal" in record.ddl
    assert "device_type LowCardinality(String)" in record.ddl
    # Always-present baseline columns.
    assert "id UUID" in record.ddl
    assert "event_name LowCardinality(String)" in record.ddl
    assert "timestamp DateTime64(3, 'UTC')" in record.ddl
    assert "_ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)" in record.ddl
