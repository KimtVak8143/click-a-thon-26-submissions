from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.agents.schema_planner import SchemaVersionRecord
from app.context.models import ApprovedContext
from app.context.repository import ContextRepositoryProtocol
from app.contracts.models import AnalyticsContract, EntityRole
from app.core.logging import get_logger

logger = get_logger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ContextAgent:
    """Publishes a new approved context version after a schema deployment.

    The base context is untouched. A new version is inserted into
    compiler_meta.context_versions with a diff that references the newly
    materialized feature table so downstream agents can enumerate it.
    """

    def __init__(
        self,
        context_repository: ContextRepositoryProtocol,
        client_factory: Callable[[], Any],
        metadata_database: str,
    ) -> None:
        if not _IDENTIFIER.fullmatch(metadata_database):
            raise ValueError("metadata database must be a safe ClickHouse identifier")
        self._context_repository = context_repository
        self._client_factory = client_factory
        self._metadata_database = metadata_database
        self._client: Any | None = None

    def update_after_schema(
        self,
        schema_record: SchemaVersionRecord,
        contract: AnalyticsContract,
        base_context: ApprovedContext,
        run_id: uuid.UUID,
    ) -> ApprovedContext:
        columns = self._introspect_columns(schema_record.database_name, schema_record.table_name)
        primary_entity_key = _primary_entity_key(contract)
        events = sorted({event.name for event in contract.events})
        created_at = datetime.now(UTC)
        feature_descriptor = {
            "table_name": schema_record.table_name,
            "database": schema_record.database_name,
            "feature_slug": contract.feature.slug,
            "entity_key": primary_entity_key,
            "events": events,
            "columns": columns,
            "schema_version_id": str(schema_record.schema_version_id),
            "run_id": str(run_id),
            "created_at": created_at.isoformat(),
        }
        updated_projection = deepcopy(base_context.projection)
        feature_tables = list(updated_projection.get("feature_tables", []))
        feature_tables = [
            entry
            for entry in feature_tables
            if not (isinstance(entry, dict) and entry.get("table_name") == schema_record.table_name)
        ]
        feature_tables.append(feature_descriptor)
        updated_projection["feature_tables"] = feature_tables

        snapshot_json = json.dumps(updated_projection, sort_keys=True, separators=(",", ":"))
        content_sha256 = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        diff_json = json.dumps(
            {
                "change_type": "schema_added",
                "feature_table_added": feature_descriptor,
                "schema_version_id": str(schema_record.schema_version_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        new_context_version_id = uuid.uuid4()
        new_version = base_context.version + 1

        self._insert_context_version(
            context_version_id=new_context_version_id,
            parent_context_version_id=base_context.context_version_id,
            schema_version_id=schema_record.schema_version_id,
            version=new_version,
            snapshot_json=snapshot_json,
            diff_json=diff_json,
            source_id=base_context.source_id,
            content_sha256=content_sha256,
            parser_version=base_context.parser_version,
            created_at=created_at,
        )
        self._insert_changelog_entry(
            context_version_id=new_context_version_id,
            summary=(f"Added {schema_record.table_name} for feature {contract.feature.slug}"),
            evidence_ids=sorted(
                {
                    "feature_specification",
                    f"schema_version:{schema_record.schema_version_id}",
                }
            ),
            created_at=created_at,
        )

        return ApprovedContext(
            context_version_id=new_context_version_id,
            version=new_version,
            content_sha256=content_sha256,
            source_id=base_context.source_id,
            parser_version=base_context.parser_version,
            projection=updated_projection,
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.warning("context_agent_client_close_failed")
            self._client = None

    # ------------------------------------------------------------- internals

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _introspect_columns(self, database: str, table_name: str) -> list[dict[str, str]]:
        try:
            client = self._get_client()
            result = client.query(
                "SELECT name, type FROM system.columns "
                "WHERE database = {database:String} AND table = {table:String} "
                "ORDER BY position",
                parameters={"database": database, "table": table_name},
            )
        except Exception:
            logger.warning(
                "context_agent_column_introspection_failed",
                extra={"database": database, "table": table_name},
            )
            return []
        columns = []
        for row in result.result_rows:
            if len(row) < 2:
                continue
            columns.append({"name": str(row[0]), "type": str(row[1])})
        return columns

    def _insert_context_version(
        self,
        *,
        context_version_id: uuid.UUID,
        parent_context_version_id: uuid.UUID | None,
        schema_version_id: uuid.UUID,
        version: int,
        snapshot_json: str,
        diff_json: str,
        source_id: uuid.UUID | None,
        content_sha256: str,
        parser_version: str,
        created_at: datetime,
    ) -> None:
        client = self._get_client()
        table = f"`{self._metadata_database}`.`context_versions`"
        client.insert(
            table,
            [
                [
                    context_version_id,
                    parent_context_version_id,
                    schema_version_id,
                    version,
                    "approved",
                    snapshot_json,
                    diff_json,
                    created_at,
                    created_at,
                    source_id,
                    content_sha256,
                    parser_version,
                ]
            ],
            column_names=[
                "context_version_id",
                "parent_context_version_id",
                "schema_version_id",
                "version",
                "status",
                "snapshot_json",
                "diff_json",
                "published_at",
                "created_at",
                "source_id",
                "content_sha256",
                "parser_version",
            ],
        )

    def _insert_changelog_entry(
        self,
        *,
        context_version_id: uuid.UUID,
        summary: str,
        evidence_ids: list[str],
        created_at: datetime,
    ) -> None:
        client = self._get_client()
        table = f"`{self._metadata_database}`.`context_changelog`"
        change_id = uuid.uuid5(context_version_id, "schema_added:1")
        client.insert(
            table,
            [
                [
                    change_id,
                    context_version_id,
                    1,
                    "schema_added",
                    summary,
                    json.dumps(evidence_ids, sort_keys=True),
                    created_at,
                ]
            ],
            column_names=[
                "change_id",
                "context_version_id",
                "sequence",
                "change_type",
                "summary",
                "evidence_ids_json",
                "created_at",
            ],
        )


def _primary_entity_key(contract: AnalyticsContract) -> str | None:
    for entity in contract.entities:
        if entity.role == EntityRole.PRIMARY:
            return entity.field_path
    return None
