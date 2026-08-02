from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.schema_planner import SchemaPlanner
from app.clickhouse.event_loader import (
    EventLoader,
    _coerce_uuid,
    _coerce_value,
    _extract_nested,
    _extract_row,
)
from app.contracts.models import AnalyticsContract
from app.profiling.profiler import SourceProfiler
from tests.test_contracts import contract_data as _contract_data

FIXTURES = Path(__file__).parent / "fixtures"


def _valid_contract() -> AnalyticsContract:
    events_path = FIXTURES / "express_checkout_events.ndjson"
    profile = SourceProfiler().profile(events_path)
    data = _contract_data.__wrapped__(profile)
    return AnalyticsContract.model_validate_with_profile(data, profile)


def test_coerce_uuid_reformats_hex32() -> None:
    assert _coerce_uuid("40e20b22bab295b7731969b160d3ebc2") == (
        "40e20b22-bab2-95b7-7319-69b160d3ebc2"
    )


def test_coerce_uuid_passes_through_already_dashed() -> None:
    value = str(uuid.uuid4())
    assert _coerce_uuid(value) == value


def test_coerce_uuid_falls_back_to_random_for_unparseable_input() -> None:
    result = _coerce_uuid("not-a-uuid")
    assert uuid.UUID(result)


def test_coerce_value_handles_decimal_and_nullable() -> None:
    assert _coerce_value("12.50", "Decimal(38, 9)") is not None
    assert _coerce_value(None, "Nullable(Float64)") is None
    assert _coerce_value("3.5", "Nullable(Float64)") == 3.5


def test_coerce_value_parses_datetime_and_date() -> None:
    parsed = _coerce_value("2026-06-08T06:00:00.000Z", "DateTime64(3, 'UTC')")
    assert parsed == datetime(2026, 6, 8, 6, 0, tzinfo=UTC)
    assert _coerce_value("2026-06-08T06:00:00.000Z", "Date") == parsed.date()


def test_coerce_value_falls_back_to_string_for_unknown_types() -> None:
    assert _coerce_value("mobile-rn", "String") == "mobile-rn"
    assert _coerce_value(42, "String") == "42"


def test_extract_nested_navigates_dotted_path() -> None:
    row = {"payment": {"amount": 42}}
    assert _extract_nested(row, "payment.amount") == 42
    assert _extract_nested(row, "payment.missing") is None
    assert _extract_nested(row, "missing.amount") is None


def test_extract_row_skips_rows_without_a_valid_timestamp() -> None:
    row: dict[str, Any] = {"event": "x", "id": "abc"}
    result = _extract_row(row, ["timestamp"], {"timestamp": "DateTime64(3, 'UTC')"}, {})
    assert result is None


def test_extract_row_excludes_materialized_event_name_column() -> None:
    # event_name is never in the insertable column list when it's derived from `event`.
    row = {"event": "checkout_started", "timestamp": "2026-06-08T06:00:00.000Z"}
    result = _extract_row(row, ["timestamp"], {"timestamp": "DateTime64(3, 'UTC')"}, {})
    assert result == [datetime(2026, 6, 8, 6, 0, tzinfo=UTC)]


class _RecordingClient:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []

    def insert(self, table: str, rows: list[list[Any]], column_names: list[str]) -> None:
        self.inserts.append((table, rows, column_names))

    def close(self) -> None:
        return None


def test_event_loader_loads_real_fixture_events() -> None:
    contract = _valid_contract()
    planner = SchemaPlanner(client_factory=lambda: None, database="clickathon1")
    columns = planner.insertable_event_columns(contract)

    client = _RecordingClient()
    loader = EventLoader(client_factory=lambda: client)

    result = loader.load(
        contract=contract,
        events_path=FIXTURES / "express_checkout_events.ndjson",
        database="clickathon1",
        table_name="express_checkout_events",
        columns=columns,
    )

    assert result.rows_read > 0
    assert result.rows_inserted == result.rows_read - result.rows_skipped
    assert client.inserts

    table, rows, column_names = client.inserts[0]
    assert table == "`clickathon1`.`express_checkout_events`"
    assert column_names == [name for name, _ in columns]
    assert sum(len(batch_rows) for _, batch_rows, _ in client.inserts) == result.rows_inserted
    # This fixture's raw events carry an explicit "event_name" key, so it's a real column here.
    assert "event_name" in column_names


def test_event_loader_close_is_idempotent() -> None:
    loader = EventLoader(client_factory=lambda: _RecordingClient())
    loader.close()
    loader.close()
