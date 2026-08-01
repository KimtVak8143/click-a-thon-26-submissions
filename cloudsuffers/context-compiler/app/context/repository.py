from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.context.models import ApprovedContext, ContextBootstrapBundle

_DATABASE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ContextRepositoryProtocol(Protocol):
    def latest_approved(self) -> ApprovedContext | None: ...

    def find_approved_by_content_sha256(self, content_sha256: str) -> ApprovedContext | None: ...

    def persist_bootstrap(self, bundle: ContextBootstrapBundle) -> None: ...

    def close(self) -> None: ...


class InMemoryContextRepository:
    """Explicit deterministic override for unit tests and local isolated tests."""

    def __init__(self, contexts: list[ApprovedContext] | None = None) -> None:
        self.contexts = list(contexts or [])
        self.bundles: list[ContextBootstrapBundle] = []

    def latest_approved(self) -> ApprovedContext | None:
        return max(self.contexts, key=lambda item: item.version, default=None)

    def find_approved_by_content_sha256(self, content_sha256: str) -> ApprovedContext | None:
        return next((item for item in self.contexts if item.content_sha256 == content_sha256), None)

    def persist_bootstrap(self, bundle: ContextBootstrapBundle) -> None:
        if self.find_approved_by_content_sha256(bundle.source.content_sha256) is not None:
            return
        self.bundles.append(bundle)
        self.contexts.append(_approved_from_bundle(bundle))

    def close(self) -> None:
        return None


class ClickHouseContextRepository:
    def __init__(self, client_factory: Callable[[], Any], metadata_database: str) -> None:
        if not _DATABASE_NAME.fullmatch(metadata_database):
            raise ValueError("metadata database must be a safe ClickHouse identifier")
        self._client_factory = client_factory
        self._database = metadata_database
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _table(self, name: str) -> str:
        return f"`{self._database}`.`{name}`"

    def latest_approved(self) -> ApprovedContext | None:
        query = (
            "SELECT context_version_id, version, content_sha256, source_id, "
            f"parser_version, snapshot_json FROM {self._table('context_versions')} "
            "WHERE status = 'approved' ORDER BY version DESC, created_at DESC LIMIT 1"
        )
        return self._approved_from_query(query)

    def find_approved_by_content_sha256(self, content_sha256: str) -> ApprovedContext | None:
        query = (
            "SELECT context_version_id, version, content_sha256, source_id, "
            f"parser_version, snapshot_json FROM {self._table('context_versions')} "
            "WHERE status = 'approved' AND content_sha256 = {content_sha256:String} "
            "ORDER BY version DESC LIMIT 1"
        )
        return self._approved_from_query(query, parameters={"content_sha256": content_sha256})

    def _approved_from_query(
        self, query: str, *, parameters: dict[str, Any] | None = None
    ) -> ApprovedContext | None:
        result = self._get_client().query(query, parameters=parameters or {})
        if not result.result_rows:
            return None
        version_id, version, checksum, source_id, parser_version, snapshot_json = (
            result.result_rows[0]
        )
        return ApprovedContext(
            context_version_id=version_id,
            version=version,
            content_sha256=checksum,
            source_id=source_id,
            parser_version=parser_version,
            projection=json.loads(snapshot_json),
        )

    def persist_bootstrap(self, bundle: ContextBootstrapBundle) -> None:
        client = self._get_client()
        now = datetime.now(UTC)
        client.insert(
            self._table("context_sources"),
            [
                [
                    bundle.source.source_id,
                    bundle.source.content_sha256,
                    bundle.source.source_name,
                    bundle.source.source_path,
                    bundle.source.source_kind,
                    bundle.source.parser_version,
                    bundle.source.source_content,
                    now,
                ]
            ],
            column_names=[
                "source_id",
                "content_sha256",
                "source_name",
                "source_path",
                "source_kind",
                "parser_version",
                "source_content",
                "created_at",
            ],
        )
        client.insert(
            self._table("context_versions"),
            [
                [
                    bundle.context_version_id,
                    bundle.parent_context_version_id,
                    bundle.schema_version_id,
                    bundle.version,
                    bundle.status.value,
                    json.dumps(bundle.projection, sort_keys=True, separators=(",", ":")),
                    "{}",
                    now,
                    now,
                    bundle.source.source_id,
                    bundle.source.content_sha256,
                    bundle.source.parser_version,
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
        self._insert_entities(client, bundle, now)
        self._insert_metrics(client, bundle, now)
        self._insert_relationships(client, bundle, now)
        self._insert_issues(client, bundle, now)
        self._insert_changelog(client, bundle, now)

    def _insert_entities(self, client: Any, bundle: ContextBootstrapBundle, now: datetime) -> None:
        client.insert(
            self._table("context_entities"),
            [
                [
                    uuid.uuid5(bundle.context_version_id, f"entity:{item.entity_id}"),
                    bundle.context_version_id,
                    item.entity_id,
                    item.name,
                    json.dumps(item.key_fields, sort_keys=True),
                    item.grain_policy,
                    json.dumps(item.evidence_ids, sort_keys=True),
                    now,
                ]
                for item in bundle.entities
            ],
            column_names=[
                "record_id",
                "context_version_id",
                "entity_id",
                "name",
                "key_fields_json",
                "grain_policy",
                "evidence_ids_json",
                "created_at",
            ],
        )

    def _insert_metrics(self, client: Any, bundle: ContextBootstrapBundle, now: datetime) -> None:
        client.insert(
            self._table("context_metrics"),
            [
                [
                    uuid.uuid5(bundle.context_version_id, f"metric:{item.metric_id}"),
                    bundle.context_version_id,
                    item.metric_id,
                    item.label,
                    item.model_dump_json(),
                    item.computability.value,
                    json.dumps(item.evidence_ids, sort_keys=True),
                    now,
                ]
                for item in bundle.metrics
            ],
            column_names=[
                "record_id",
                "context_version_id",
                "metric_id",
                "label",
                "definition_json",
                "computability",
                "evidence_ids_json",
                "created_at",
            ],
        )

    def _insert_relationships(
        self, client: Any, bundle: ContextBootstrapBundle, now: datetime
    ) -> None:
        client.insert(
            self._table("context_relationships"),
            [
                [
                    uuid.uuid5(bundle.context_version_id, f"relationship:{item.relationship_id}"),
                    bundle.context_version_id,
                    item.relationship_id,
                    item.source_entity,
                    item.target_entity,
                    item.source_field,
                    item.target_field,
                    item.cardinality,
                    item.temporal_constraint,
                    json.dumps(item.evidence_ids, sort_keys=True),
                    now,
                ]
                for item in bundle.relationships
            ],
            column_names=[
                "record_id",
                "context_version_id",
                "relationship_id",
                "source_entity",
                "target_entity",
                "source_field",
                "target_field",
                "cardinality",
                "temporal_constraint",
                "evidence_ids_json",
                "created_at",
            ],
        )

    def _insert_issues(self, client: Any, bundle: ContextBootstrapBundle, now: datetime) -> None:
        client.insert(
            self._table("context_issues"),
            [
                [
                    uuid.uuid5(bundle.context_version_id, f"issue:{item.issue_code}"),
                    bundle.context_version_id,
                    item.issue_code,
                    item.category,
                    item.severity,
                    item.status,
                    item.title,
                    item.description,
                    json.dumps(
                        {
                            "evidence_ids": item.evidence_ids,
                            "affected_artifacts": item.affected_artifacts,
                            "hypothesis": item.hypothesis,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    None,
                    now,
                    None,
                    now,
                ]
                for item in bundle.issues
            ],
            column_names=[
                "issue_id",
                "context_version_id",
                "issue_code",
                "category",
                "severity",
                "status",
                "title",
                "description",
                "evidence_json",
                "resolution",
                "detected_at",
                "resolved_at",
                "updated_at",
            ],
        )

    def _insert_changelog(self, client: Any, bundle: ContextBootstrapBundle, now: datetime) -> None:
        client.insert(
            self._table("context_changelog"),
            [
                [
                    uuid.uuid5(bundle.context_version_id, f"change:{index}"),
                    bundle.context_version_id,
                    index,
                    item["change_type"],
                    item["summary"],
                    json.dumps(item["evidence_ids"], sort_keys=True),
                    now,
                ]
                for index, item in enumerate(bundle.changelog, start=1)
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

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _approved_from_bundle(bundle: ContextBootstrapBundle) -> ApprovedContext:
    return ApprovedContext(
        context_version_id=bundle.context_version_id,
        version=bundle.version,
        content_sha256=bundle.source.content_sha256,
        source_id=bundle.source.source_id,
        parser_version=bundle.source.parser_version,
        projection=bundle.projection,
    )
