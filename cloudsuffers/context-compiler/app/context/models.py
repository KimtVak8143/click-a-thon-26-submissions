from __future__ import annotations

import json
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.profiling.models import Sha256


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class MetricComputability(StrEnum):
    COMPUTABLE = "computable"
    BLOCKED = "blocked"
    EXTERNAL = "external"


class ContextSource(ContextModel):
    source_id: UUID
    content_sha256: Sha256
    source_name: str
    source_path: str
    source_kind: str
    parser_version: str
    source_content: str


class ContextEntityRecord(ContextModel):
    entity_id: str
    name: str
    key_fields: list[str]
    grain_policy: str
    evidence_ids: list[str]


class ContextMetricRecord(ContextModel):
    metric_id: str
    label: str
    numerator: str
    denominator: str
    entity_grain: str
    entity_key: str
    time_attribution: str
    analysis_window: str
    dimensions: list[str]
    zero_denominator_behavior: str
    deduplication_policy: str
    currency_policy: str | None
    computability: MetricComputability
    evidence_ids: list[str]


class ContextRelationshipRecord(ContextModel):
    relationship_id: str
    source_entity: str
    target_entity: str
    source_field: str
    target_field: str
    cardinality: str
    temporal_constraint: str | None
    evidence_ids: list[str]


class ContextIssueRecord(ContextModel):
    issue_code: str
    category: str
    severity: str
    status: str
    title: str
    description: str
    evidence_ids: list[str]
    affected_artifacts: list[str]
    hypothesis: bool = False


class ContextBootstrapBundle(ContextModel):
    context_version_id: UUID
    parent_context_version_id: UUID | None = None
    schema_version_id: UUID = UUID(int=0)
    version: int = Field(ge=1)
    status: ContextStatus
    source: ContextSource
    projection: dict[str, Any]
    entities: list[ContextEntityRecord]
    metrics: list[ContextMetricRecord]
    relationships: list[ContextRelationshipRecord]
    issues: list[ContextIssueRecord]
    changelog: list[dict[str, Any]]


class ApprovedContext(ContextModel):
    context_version_id: UUID
    version: int = Field(ge=1)
    content_sha256: Sha256
    source_id: UUID
    parser_version: str
    projection: dict[str, Any]

    @property
    def evidence_ids(self) -> list[str]:
        values = self.projection.get("evidence_ids", [])
        return sorted(item for item in values if isinstance(item, str))

    def compact_json(self) -> str:
        compact = {
            "source_precedence": self.projection.get("source_precedence", []),
            "grain_policy": self.projection.get("grain_policy", {}),
            "canonical_funnel": self.projection.get("canonical_funnel", []),
            "supporting_events": self.projection.get("supporting_events", []),
            "entities": [
                {
                    key: item.get(key)
                    for key in ("entity_id", "key_fields", "grain_policy", "evidence_ids")
                }
                for item in self.projection.get("entities", [])
                if isinstance(item, dict)
            ],
            "metrics": [
                {
                    key: item.get(key)
                    for key in (
                        "metric_id",
                        "numerator",
                        "denominator",
                        "entity_grain",
                        "entity_key",
                        "currency_policy",
                        "computability",
                        "evidence_ids",
                    )
                }
                for item in self.projection.get("metrics", [])
                if isinstance(item, dict)
            ],
            "relationships": self.projection.get("relationships", []),
            "issues": self.projection.get("issues", []),
        }
        return json.dumps(compact, sort_keys=True, separators=(",", ":"))
