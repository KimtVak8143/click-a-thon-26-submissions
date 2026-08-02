from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.agents.schema_planner import _safe_identifier
from app.contracts.models import AnalyticsContract, FieldDefinition
from app.core.logging import get_logger
from app.profiling.profiler import _parse_timestamp

logger = get_logger(__name__)

_BATCH_SIZE = 5_000
_UUID_DASHED = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_UUID_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
_TIMESTAMP_FIELDS = ("timestamp", "event_time")
_EVENT_NAME_FIELDS = ("event", "event_name")


@dataclass
class EventIngestionResult:
    rows_read: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0


class EventLoader:
    """Loads a feature's raw NDJSON event sample into its deployed ClickHouse table.

    The Instrumentation Agent's contract and the Schema Planner's DDL describe the
    table's shape; this class maps each raw JSON row onto that shape so the
    Analytics Agent has real data to query in the same run that deployed the schema.
    """

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.warning("event_loader_client_close_failed")
            self._client = None

    def load(
        self,
        *,
        contract: AnalyticsContract,
        events_path: Path,
        database: str,
        table_name: str,
        columns: list[tuple[str, str]],
    ) -> EventIngestionResult:
        field_by_column = {
            _column_name(definition): definition
            for definition in contract.fields
            if not definition.spec_only
        }
        column_names = [name for name, _ in columns]
        column_types = dict(columns)
        target = f"`{database}`.`{table_name}`"
        client = self._get_client()

        result = EventIngestionResult()
        batch: list[list[Any]] = []
        with events_path.open("rb") as source:
            for raw_line in source:
                if not raw_line.strip():
                    continue
                result.rows_read += 1
                row = _parse_row(raw_line)
                if row is None:
                    result.rows_skipped += 1
                    continue
                values = _extract_row(row, column_names, column_types, field_by_column)
                if values is None:
                    result.rows_skipped += 1
                    continue
                batch.append(values)
                if len(batch) >= _BATCH_SIZE:
                    client.insert(target, batch, column_names=column_names)
                    result.rows_inserted += len(batch)
                    batch = []
        if batch:
            client.insert(target, batch, column_names=column_names)
            result.rows_inserted += len(batch)
        return result


def _column_name(definition: FieldDefinition) -> str:
    return _safe_identifier(definition.source_path)


def _parse_row(raw_line: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw_line)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _first_present(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    return next((row[name] for name in names if name in row), None)


def _extract_nested(row: dict[str, Any], dotted_path: str) -> Any:
    current: Any = row
    for segment in dotted_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _coerce_uuid(raw: Any) -> str:
    if isinstance(raw, str):
        candidate = raw.strip()
        if _UUID_DASHED.fullmatch(candidate):
            return candidate
        if _UUID_HEX32.fullmatch(candidate):
            return (
                f"{candidate[0:8]}-{candidate[8:12]}-{candidate[12:16]}-"
                f"{candidate[16:20]}-{candidate[20:32]}"
            )
    return str(uuid.uuid4())


def _coerce_value(value: Any, clickhouse_type: str) -> Any:
    if value is None:
        return None
    inner = clickhouse_type
    if inner.startswith("Nullable(") and inner.endswith(")"):
        inner = inner[len("Nullable(") : -1]
    if inner.startswith("DateTime"):
        return _parse_timestamp(value)
    if inner == "Date":
        parsed = _parse_timestamp(value)
        return parsed.date() if parsed is not None else None
    if inner.startswith("Decimal"):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    if inner in {"Int64", "UInt64", "Int32", "UInt32"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if inner in {"Float64", "Float32"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if inner == "Bool":
        return bool(value)
    if inner.startswith("Array("):
        items = value if isinstance(value, list) else [value]
        return [str(item) for item in items]
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _extract_row(
    row: dict[str, Any],
    column_names: list[str],
    column_types: dict[str, str],
    field_by_column: dict[str, FieldDefinition],
) -> list[Any] | None:
    values: list[Any] = []
    for name in column_names:
        if name == "id":
            values.append(_coerce_uuid(_first_present(row, ("id", "event_id"))))
            continue
        if name == "timestamp":
            timestamp = _parse_timestamp(_first_present(row, _TIMESTAMP_FIELDS))
            if timestamp is None:
                return None
            values.append(timestamp)
            continue
        if name == "event_name":
            event_value = _first_present(row, _EVENT_NAME_FIELDS)
            values.append(event_value if isinstance(event_value, str) else "")
            continue
        definition = field_by_column.get(name)
        if definition is None:
            values.append(None)
            continue
        raw_value = _extract_nested(row, definition.source_path)
        values.append(_coerce_value(raw_value, column_types[name]))
    return values
