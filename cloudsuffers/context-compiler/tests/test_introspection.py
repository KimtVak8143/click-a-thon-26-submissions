from __future__ import annotations

from typing import Any

from app.clickhouse.introspection import list_columns, list_tables


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class _FakeClient:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, *, raises: bool = False) -> None:
        self._rows = rows or []
        self._raises = raises
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> _Result:
        self.queries.append((statement, parameters or {}))
        if self._raises:
            raise RuntimeError("boom")
        return _Result(self._rows)


def test_list_columns_returns_name_type_pairs() -> None:
    client = _FakeClient(rows=[("id", "UUID"), ("timestamp", "DateTime64(3, 'UTC')")])
    columns = list_columns(client, "clickathon1", "express_checkout_events")
    assert columns == [
        {"name": "id", "type": "UUID"},
        {"name": "timestamp", "type": "DateTime64(3, 'UTC')"},
    ]
    statement, parameters = client.queries[0]
    assert "system.columns" in statement
    assert parameters == {"database": "clickathon1", "table": "express_checkout_events"}


def test_list_columns_returns_empty_list_on_query_failure() -> None:
    client = _FakeClient(raises=True)
    assert list_columns(client, "clickathon1", "missing_table") == []


def test_list_columns_skips_short_rows() -> None:
    client = _FakeClient(rows=[("id",)])
    assert list_columns(client, "clickathon1", "t") == []


def test_list_tables_returns_names_in_query_order() -> None:
    # Ordering is delegated to the SQL's own ORDER BY; this just checks pass-through.
    client = _FakeClient(rows=[("express_checkout_events",), ("application_started",)])
    tables = list_tables(client, "clickathon1")
    assert tables == ["express_checkout_events", "application_started"]
    statement, parameters = client.queries[0]
    assert "system.tables" in statement
    assert "ORDER BY name" in statement
    assert parameters == {"database": "clickathon1"}


def test_list_tables_returns_empty_list_on_query_failure() -> None:
    client = _FakeClient(raises=True)
    assert list_tables(client, "clickathon1") == []
