from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.contracts.models import AnalyticsContract, EntityRole
from app.core.logging import get_logger

logger = get_logger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class SchemaStrategy:
    strategy_name: str
    table_name: str
    ddl_statements: list[str] = field(default_factory=list)


@dataclass
class SchemaVersionRecord:
    schema_version_id: UUID
    run_id: UUID
    contract_id: UUID
    feature_slug: str
    version: int
    database_name: str
    object_inventory_json: str
    ddl: str
    deployed_at: datetime | None = None
    strategy_name: str = "dedicated_event_table"
    table_name: str = ""
    created_at: datetime | None = None


class SchemaPlanner:
    """Deterministic ClickHouse schema planner.

    Produces DDL for a dedicated feature event table (and an optional funnel
    materialized view) from a validated AnalyticsContract, then optionally
    deploys the DDL against ClickHouse.
    """

    def __init__(
        self,
        client_factory: Callable[[], Any],
        database: str,
        *,
        retention_days: int = 730,
    ) -> None:
        if not _IDENTIFIER.fullmatch(database):
            raise ValueError("database must be a safe ClickHouse identifier")
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self._client_factory = client_factory
        self._database = database
        self._retention_days = retention_days
        self._client: Any | None = None

    # ------------------------------------------------------------------ plan

    def plan(
        self,
        contract: AnalyticsContract,
        run_id: UUID,
        *,
        dry_run: bool = False,
    ) -> SchemaVersionRecord:
        strategy = self._build_strategy(contract)
        object_inventory = self._object_inventory(strategy)
        ddl = "\n\n".join(strategy.ddl_statements)
        record = SchemaVersionRecord(
            schema_version_id=uuid.uuid4(),
            run_id=run_id,
            contract_id=self._contract_id(contract),
            feature_slug=contract.feature.slug,
            version=1,
            database_name=self._database,
            object_inventory_json=json.dumps(object_inventory, sort_keys=True),
            ddl=ddl,
            deployed_at=None,
            strategy_name=strategy.strategy_name,
            table_name=strategy.table_name,
            created_at=datetime.now(UTC),
        )
        if not dry_run:
            self._deploy(strategy)
            record.deployed_at = datetime.now(UTC)
        return record

    # --------------------------------------------------------------- persist

    def persist(self, record: SchemaVersionRecord, metadata_database: str) -> None:
        if not _IDENTIFIER.fullmatch(metadata_database):
            raise ValueError("metadata database must be a safe ClickHouse identifier")
        client = self._get_client()
        table = f"`{metadata_database}`.`schema_versions`"
        created_at = record.created_at or datetime.now(UTC)
        client.insert(
            table,
            [
                [
                    record.schema_version_id,
                    record.run_id,
                    record.contract_id,
                    record.feature_slug,
                    record.version,
                    record.database_name,
                    record.object_inventory_json,
                    record.ddl,
                    record.deployed_at,
                    created_at,
                ]
            ],
            column_names=[
                "schema_version_id",
                "run_id",
                "contract_id",
                "feature_slug",
                "version",
                "database_name",
                "object_inventory_json",
                "ddl",
                "deployed_at",
                "created_at",
            ],
        )

    # ---------------------------------------------------------- list_deployed

    def list_deployed(
        self,
        metadata_database: str,
        feature_slug: str | None = None,
    ) -> list[SchemaVersionRecord]:
        if not _IDENTIFIER.fullmatch(metadata_database):
            raise ValueError("metadata database must be a safe ClickHouse identifier")
        client = self._get_client()
        table = f"`{metadata_database}`.`schema_versions`"
        base_query = (
            "SELECT schema_version_id, run_id, contract_id, feature_slug, version, "
            "database_name, object_inventory_json, ddl, deployed_at, created_at "
            f"FROM {table}"
        )
        if feature_slug is not None:
            query = (
                f"{base_query} WHERE feature_slug = {{feature_slug:String}} "
                "ORDER BY created_at DESC LIMIT 100"
            )
            result = client.query(query, parameters={"feature_slug": feature_slug})
        else:
            query = f"{base_query} ORDER BY created_at DESC LIMIT 100"
            result = client.query(query, parameters={})
        records: list[SchemaVersionRecord] = []
        for row in result.result_rows:
            (
                schema_version_id,
                run_id,
                contract_id,
                slug,
                version,
                database_name,
                inventory_json,
                ddl_text,
                deployed_at,
                created_at,
            ) = row
            table_name = _first_table_name(inventory_json)
            strategy_name = _strategy_from_inventory(inventory_json)
            records.append(
                SchemaVersionRecord(
                    schema_version_id=_as_uuid(schema_version_id),
                    run_id=_as_uuid(run_id),
                    contract_id=_as_uuid(contract_id),
                    feature_slug=slug,
                    version=version,
                    database_name=database_name,
                    object_inventory_json=inventory_json,
                    ddl=ddl_text,
                    deployed_at=deployed_at,
                    strategy_name=strategy_name,
                    table_name=table_name,
                    created_at=created_at,
                )
            )
        return records

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.warning("schema_planner_client_close_failed")
            self._client = None

    # ------------------------------------------------------------- internals

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _build_strategy(self, contract: AnalyticsContract) -> SchemaStrategy:
        table_name = self._event_table_name(contract.feature.slug)
        ddl_statements = [self._build_event_table_ddl(contract, table_name)]
        strategy_name = "dedicated_event_table"
        if contract.funnels:
            ddl_statements.append(self._build_funnel_mv_ddl(contract, table_name))
            strategy_name = "materialized_aggregate"
        return SchemaStrategy(
            strategy_name=strategy_name,
            table_name=table_name,
            ddl_statements=ddl_statements,
        )

    def _event_table_name(self, feature_slug: str) -> str:
        safe_slug = _safe_identifier(feature_slug)
        return f"{safe_slug}_events"

    def _build_event_table_ddl(self, contract: AnalyticsContract, table_name: str) -> str:
        primary_key = _primary_entity_key(contract)
        observed_paths = {
            definition.source_path for definition in contract.fields if not definition.spec_only
        }
        columns: list[str] = [
            "id UUID",
            "timestamp DateTime64(3, 'UTC')",
            "_ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)",
        ]
        seen: set[str] = {"id", "timestamp", "_ingested_at"}
        if "event_name" in observed_paths:
            columns.append("event_name LowCardinality(String)")
            seen.add("event_name")
        for definition in contract.fields:
            if definition.spec_only:
                continue
            column_name = _safe_identifier(definition.source_path)
            if column_name in seen:
                continue
            seen.add(column_name)
            columns.append(f"{column_name} {definition.clickhouse_type}")
        if "event_name" not in seen:
            if "event" in seen:
                columns.append(
                    "event_name LowCardinality(String) MATERIALIZED toLowCardinality(event)"
                )
            else:
                columns.append("event_name LowCardinality(String)")
        columns_block = ",\n    ".join(columns)
        order_by = (
            f"({primary_key}, event_name, timestamp)" if primary_key else "(event_name, timestamp)"
        )
        return (
            f"CREATE TABLE IF NOT EXISTS `{self._database}`.`{table_name}`\n"
            f"(\n    {columns_block}\n)\n"
            "ENGINE = MergeTree\n"
            "PARTITION BY toYYYYMM(timestamp)\n"
            f"ORDER BY {order_by}\n"
            f"TTL timestamp + INTERVAL {self._retention_days} DAY DELETE"
        )

    def _build_funnel_mv_ddl(self, contract: AnalyticsContract, table_name: str) -> str:
        primary_key = _primary_entity_key(contract) or "id"
        safe_slug = _safe_identifier(contract.feature.slug)
        mv_name = f"{safe_slug}_funnel_daily_mv"
        return (
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS "
            f"`{self._database}`.`{mv_name}`\n"
            "ENGINE = AggregatingMergeTree()\n"
            "PARTITION BY toYYYYMM(date)\n"
            "ORDER BY (date, event_name)\n"
            "AS SELECT\n"
            "    toDate(timestamp) AS date,\n"
            "    event_name,\n"
            f"    uniqExactState({primary_key}) AS unique_entity_count\n"
            f"FROM `{self._database}`.`{table_name}`\n"
            "GROUP BY date, event_name"
        )

    def _object_inventory(self, strategy: SchemaStrategy) -> dict[str, Any]:
        objects: list[str] = [strategy.table_name]
        for statement in strategy.ddl_statements:
            match = re.search(
                r"CREATE MATERIALIZED VIEW IF NOT EXISTS `[^`]+`\.`([^`]+)`",
                statement,
            )
            if match and match.group(1) not in objects:
                objects.append(match.group(1))
        return {
            "strategy": strategy.strategy_name,
            "database": self._database,
            "tables": objects,
        }

    def _deploy(self, strategy: SchemaStrategy) -> None:
        client = self._get_client()
        for statement in strategy.ddl_statements:
            client.command(statement)

    def _contract_id(self, contract: AnalyticsContract) -> UUID:
        namespace = uuid.UUID("6b1c1c8a-2d20-5a3f-9c1e-4b0e7a8b1c1d")
        seed = "|".join(
            [
                contract.feature.slug,
                contract.source.events_sha256,
                contract.source.spec_sha256,
            ]
        )
        return uuid.uuid5(namespace, seed)


def _safe_identifier(value: str) -> str:
    """Return a ClickHouse-safe snake_case identifier derived from *value*."""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "field"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def _primary_entity_key(contract: AnalyticsContract) -> str | None:
    for entity in contract.entities:
        if entity.role == EntityRole.PRIMARY:
            return _safe_identifier(entity.field_path)
    return None


def _first_table_name(inventory_json: str) -> str:
    try:
        payload = json.loads(inventory_json)
        tables = payload.get("tables") if isinstance(payload, dict) else None
        if isinstance(tables, list) and tables and isinstance(tables[0], str):
            return tables[0]
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    return ""


def _strategy_from_inventory(inventory_json: str) -> str:
    try:
        payload = json.loads(inventory_json)
        strategy = payload.get("strategy") if isinstance(payload, dict) else None
        if isinstance(strategy, str):
            return strategy
    except (json.JSONDecodeError, TypeError, ValueError):
        return "dedicated_event_table"
    return "dedicated_event_table"


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
