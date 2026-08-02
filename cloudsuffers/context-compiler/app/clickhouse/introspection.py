from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def list_columns(client: Any, database: str, table: str) -> list[dict[str, str]]:
    """Return [{"name": ..., "type": ...}, ...] for a table's columns, in position order."""
    try:
        result = client.query(
            "SELECT name, type FROM system.columns "
            "WHERE database = {database:String} AND table = {table:String} "
            "ORDER BY position",
            parameters={"database": database, "table": table},
        )
    except Exception:
        logger.warning(
            "column_introspection_failed",
            extra={"database": database, "table": table},
        )
        return []
    columns = []
    for row in result.result_rows:
        if len(row) < 2:
            continue
        columns.append({"name": str(row[0]), "type": str(row[1])})
    return columns


def list_tables(client: Any, database: str) -> list[str]:
    """Return every table name in `database`, excluding ClickHouse-internal tables."""
    try:
        result = client.query(
            "SELECT name FROM system.tables "
            "WHERE database = {database:String} AND NOT startsWith(name, '.') "
            "ORDER BY name",
            parameters={"database": database},
        )
    except Exception:
        logger.warning("table_discovery_failed", extra={"database": database})
        return []
    return [str(row[0]) for row in result.result_rows if row]
