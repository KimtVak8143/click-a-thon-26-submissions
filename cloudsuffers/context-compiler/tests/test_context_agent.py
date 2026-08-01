from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.agents.context_agent import ContextAgent
from app.agents.schema_planner import SchemaPlanner
from app.context.bootstrap import build_base_context_bundle
from app.context.repository import InMemoryContextRepository
from app.contracts.models import AnalyticsContract
from app.profiling.profiler import SourceProfiler
from tests.test_contracts import contract_data as _contract_data

_ = _contract_data
BASE_CONTEXT = Path(__file__).parents[1] / "docs" / "base_context.md"


class RecordingClient:
    def __init__(self, column_rows: list[list[Any]] | None = None) -> None:
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self._rows = column_rows or []

    def query(self, statement: str, parameters: dict[str, Any] | None = None):
        self.queries.append((statement, parameters or {}))

        class Result:
            def __init__(self, rows: list[list[Any]]) -> None:
                self.result_rows = rows
                self.column_names = ["name", "type"]

        return Result(self._rows)

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))

    def command(self, statement: str) -> None:
        return None

    def close(self) -> None:
        return None


def _bootstrap_context():
    repository = InMemoryContextRepository()
    repository.persist_bootstrap(build_base_context_bundle(BASE_CONTEXT))
    return repository, repository.latest_approved()


def _valid_contract() -> AnalyticsContract:
    events_path = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"
    profile = SourceProfiler().profile(events_path)
    data = _contract_data.__wrapped__(profile)
    return AnalyticsContract.model_validate_with_profile(data, profile)


def test_context_agent_publishes_new_version_and_changelog_entry() -> None:
    contract = _valid_contract()
    repository, base_context = _bootstrap_context()
    assert base_context is not None
    planner_client = RecordingClient()
    schema_planner = SchemaPlanner(lambda: planner_client, database="atlys_analytics")
    schema_record = schema_planner.plan(contract, uuid.uuid4(), dry_run=True)

    client = RecordingClient(column_rows=[["id", "UUID"], ["event_name", "LowCardinality(String)"]])
    agent = ContextAgent(
        context_repository=repository,
        client_factory=lambda: client,
        metadata_database="compiler_meta",
    )

    updated = agent.update_after_schema(schema_record, contract, base_context, uuid.uuid4())

    assert updated.version == base_context.version + 1
    assert updated.context_version_id != base_context.context_version_id
    feature_tables = updated.projection.get("feature_tables")
    assert isinstance(feature_tables, list) and len(feature_tables) == 1
    assert feature_tables[0]["table_name"] == "express_checkout_events"
    assert feature_tables[0]["entity_key"] == "application_id"
    # Expect two inserts: context_versions and context_changelog.
    tables_inserted = [item[0] for item in client.inserts]
    assert any(name.endswith("`context_versions`") for name in tables_inserted)
    assert any(name.endswith("`context_changelog`") for name in tables_inserted)


def test_context_agent_rejects_unsafe_metadata_database() -> None:
    repository, _ = _bootstrap_context()
    try:
        ContextAgent(
            context_repository=repository,
            client_factory=lambda: RecordingClient(),
            metadata_database="drop; --",
        )
    except ValueError:
        return
    raise AssertionError("expected ValueError for unsafe database identifier")


def test_context_agent_handles_column_introspection_failure() -> None:
    contract = _valid_contract()
    repository, base_context = _bootstrap_context()
    assert base_context is not None
    planner_client = RecordingClient()
    schema_planner = SchemaPlanner(lambda: planner_client, database="atlys_analytics")
    schema_record = schema_planner.plan(contract, uuid.uuid4(), dry_run=True)

    class FailingClient(RecordingClient):
        def query(self, statement: str, parameters: dict[str, Any] | None = None):
            raise RuntimeError("no clickhouse")

    client = FailingClient()
    agent = ContextAgent(
        context_repository=repository,
        client_factory=lambda: client,
        metadata_database="compiler_meta",
    )

    updated = agent.update_after_schema(schema_record, contract, base_context, uuid.uuid4())

    feature_tables = updated.projection.get("feature_tables")
    assert feature_tables and feature_tables[0]["columns"] == []
    # Even when column introspection fails, we still record the new version.
    assert any(name.endswith("`context_versions`") for name, *_ in client.inserts)


def test_context_agent_diff_json_includes_change_type_schema_added() -> None:
    contract = _valid_contract()
    repository, base_context = _bootstrap_context()
    assert base_context is not None
    planner_client = RecordingClient()
    schema_planner = SchemaPlanner(lambda: planner_client, database="atlys_analytics")
    schema_record = schema_planner.plan(contract, uuid.uuid4(), dry_run=True)
    client = RecordingClient()
    agent = ContextAgent(
        context_repository=repository,
        client_factory=lambda: client,
        metadata_database="compiler_meta",
    )

    agent.update_after_schema(schema_record, contract, base_context, uuid.uuid4())
    version_rows: list[list[object]] = []
    for name, rows, _cols in client.inserts:
        if name.endswith("`context_versions`"):
            version_rows.extend(rows)
    version_insert = version_rows[0]
    diff_json = version_insert[6]
    diff = json.loads(diff_json)
    assert diff["change_type"] == "schema_added"
    assert diff["schema_version_id"] == str(schema_record.schema_version_id)
