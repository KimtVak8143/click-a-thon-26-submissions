from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.contracts.intent import ContractIntent, infer_observed_semantic_type
from app.contracts.models import RuleSeverity, SemanticType
from app.profiling.models import FieldProfile, SourceProfile


@dataclass(frozen=True)
class CompilationMetadata:
    observed_event_count: int
    observed_field_count: int
    entity_count: int
    funnel_count: int
    metric_count: int
    dimension_count: int
    spec_only_field_count: int

    def as_dict(self) -> dict[str, int]:
        return {
            "observed_event_count": self.observed_event_count,
            "observed_field_count": self.observed_field_count,
            "entity_count": self.entity_count,
            "funnel_count": self.funnel_count,
            "metric_count": self.metric_count,
            "dimension_count": self.dimension_count,
            "spec_only_field_count": self.spec_only_field_count,
        }


def compile_contract_payload(
    intent: ContractIntent,
    source_profile: SourceProfile,
    *,
    spec_sha256: str,
    context_version_id: UUID | None = None,
    context_content_sha256: str | None = None,
    context_evidence_ids: list[str] | None = None,
) -> tuple[dict[str, Any], CompilationMetadata]:
    """Expand validated semantic intent into a complete AnalyticsContract payload."""

    dimension_names = _stable_names([item.field_path for item in intent.dimensions])
    observed_fields = sorted(source_profile.fields, key=lambda item: item.path)
    spec_only_dimensions = sorted(
        (item for item in intent.dimensions if item.spec_only),
        key=lambda item: item.field_path,
    )
    field_names = _stable_names(
        [item.path for item in observed_fields] + [item.field_path for item in spec_only_dimensions]
    )
    candidate_paths = {item.field_path for item in source_profile.candidate_identifiers}
    entities_by_id = {item.id: item for item in intent.entities}

    fields = [
        _compile_observed_field(
            item,
            field_name=field_names[item.path],
            is_identifier=item.path in candidate_paths,
        )
        for item in observed_fields
    ]
    fields.extend(
        {
            "name": field_names[item.field_path],
            "source_path": item.field_path,
            "semantic_type": item.semantic_type.value,
            "clickhouse_type": _clickhouse_type(item.semantic_type, nullable=True),
            "observed_null_rate": None,
            "event_scope": [],
            "spec_only": True,
            "description": f"Specification-only field {item.field_path}",
        }
        for item in spec_only_dimensions
    )

    event_keys: dict[str, list[str]] = {
        event.event_name: [
            entity.key_field
            for entity in intent.entities
            if event.event_name
            in next(
                field.observed_in_events
                for field in source_profile.fields
                if field.path == entity.key_field
            )
        ]
        for event in source_profile.event_profile.events
    }

    events = [
        {
            "name": event.event_name,
            "description": f"Observed source event {event.event_name}",
            "entity_keys": event_keys[event.event_name],
            "spec_only": False,
        }
        for event in sorted(source_profile.event_profile.events, key=lambda item: item.event_name)
    ]
    entities = [
        {
            "name": item.id,
            "field_path": item.key_field,
            "description": item.description,
            "role": item.role.value,
            "stable": True,
            "evidence_ids": item.evidence_ids,
        }
        for item in intent.entities
    ]
    funnels = [
        {
            "name": item.name,
            "entity_key": entities_by_id[item.entity_id].key_field,
            "steps": [
                {"order": index, "event_name": event_name, "label": None}
                for index, event_name in enumerate(item.ordered_events, start=1)
            ],
            "ordered": item.ordered,
            "description": item.description,
            "workflow_grain": item.workflow_grain,
            "attribution_window": item.attribution_window,
            "evidence_ids": item.evidence_ids,
        }
        for item in intent.funnels
    ]
    metrics = [
        {
            "name": item.name,
            "description": item.description,
            "numerator": item.numerator,
            "denominator": item.denominator,
            "entity_key": entities_by_id[item.entity_id].key_field,
            "aggregation_grain": item.aggregation_grain,
            "window": item.analysis_window,
            "zero_denominator_behavior": item.zero_denominator_behavior.value,
            "value_type": item.value_type.value,
            "currency_dimension": (
                dimension_names[item.currency_dimension_field]
                if item.currency_dimension_field is not None
                else None
            ),
            "fx_normalization_rule": item.fx_normalization_rule,
            "time_attribution": item.time_attribution,
            "deduplication_policy": item.deduplication_policy,
            "dimensions": item.dimensions,
            "computability": item.computability.value,
            "evidence_ids": item.evidence_ids,
            "duration_start_event": item.duration_start_event,
            "duration_end_event": item.duration_end_event,
        }
        for item in intent.metrics
    ]
    dimensions = [
        {
            "name": dimension_names[item.field_path],
            "field_path": item.field_path,
            "description": item.purpose,
            "null_handling": item.null_handling.value,
            "normalization_rules": [
                rule.model_dump(mode="json") for rule in item.normalization_rules
            ],
        }
        for item in intent.dimensions
    ]
    relationships = [
        {
            "name": item.id,
            "from_entity": item.source_entity_id,
            "to_entity": item.target_entity_id,
            "from_field": item.source_field,
            "to_field": item.target_field,
            "cardinality": item.cardinality.value,
            "temporal_constraint": item.temporal_constraint,
            "description": item.description,
        }
        for item in intent.relationships
    ]

    payload: dict[str, Any] = {
        "contract_version": "1.0",
        "feature": intent.feature.model_dump(mode="json"),
        "source": {
            "spec_sha256": spec_sha256,
            "events_sha256": source_profile.file.sha256,
            "row_count": source_profile.file.valid_row_count,
            "observed_window": {
                "start": source_profile.time_coverage.minimum,
                "end": source_profile.time_coverage.maximum,
            },
        },
        "grain": "one observed event record",
        "primary_entity": intent.primary_entity_id,
        "secondary_entities": [
            item.id for item in intent.entities if item.id != intent.primary_entity_id
        ],
        "entities": entities,
        "events": events,
        "fields": fields,
        "funnels": funnels,
        "metrics": metrics,
        "dimensions": dimensions,
        "relationships": relationships,
        "data_quality_rules": _compile_data_quality_rules(source_profile, intent),
        "observations": [item.model_dump(mode="json") for item in intent.observations]
        + _compile_profile_observations(source_profile),
        "assumptions": [item.model_dump(mode="json") for item in intent.assumptions],
        "open_questions": [item.model_dump(mode="json") for item in intent.open_questions],
        "context_version_id": context_version_id,
        "context_content_sha256": context_content_sha256,
        "evidence_ids": sorted(set(context_evidence_ids or [])),
    }
    metadata = CompilationMetadata(
        observed_event_count=len(events),
        observed_field_count=len(observed_fields),
        entity_count=len(entities),
        funnel_count=len(funnels),
        metric_count=len(metrics),
        dimension_count=len(dimensions),
        spec_only_field_count=len(spec_only_dimensions),
    )
    return payload, metadata


def _compile_observed_field(
    field: FieldProfile,
    *,
    field_name: str,
    is_identifier: bool,
) -> dict[str, Any]:
    semantic_type = infer_observed_semantic_type(field, is_identifier=is_identifier)
    return {
        "name": field_name,
        "source_path": field.path,
        "semantic_type": semantic_type.value,
        "clickhouse_type": _clickhouse_type(
            semantic_type,
            nullable=field.null_count > 0 or field.presence_rate < 1,
        ),
        "observed_null_rate": field.null_rate,
        "event_scope": sorted(field.observed_in_events),
        "spec_only": False,
        "description": f"Observed source field {field.path}",
    }


def _clickhouse_type(semantic_type: SemanticType, *, nullable: bool) -> str:
    base = {
        SemanticType.STRING: "String",
        SemanticType.BOOLEAN: "Bool",
        SemanticType.INTEGER: "Int64",
        SemanticType.NUMBER: "Float64",
        SemanticType.DECIMAL: "Decimal(38, 9)",
        SemanticType.CURRENCY: "Decimal(38, 9)",
        SemanticType.DATETIME: "DateTime64(3, 'UTC')",
        SemanticType.DATE: "Date",
        SemanticType.IDENTIFIER: "String",
        SemanticType.COUNTRY_CODE: "FixedString(2)",
        SemanticType.ARRAY: "Array(String)",
        SemanticType.OBJECT: "String",
    }[semantic_type]
    return f"Nullable({base})" if nullable else base


def _compile_data_quality_rules(
    source_profile: SourceProfile,
    intent: ContractIntent,
) -> list[dict[str, Any]]:
    rules = [
        {
            "name": f"profile_{index}_{_safe_name(item.code.value)}",
            "description": (
                f"SourceProfile observed {item.code.value} in {item.count} source records"
            ),
            "severity": (
                RuleSeverity.WARNING.value
                if item.severity == "warning"
                else RuleSeverity.INFO.value
            ),
            "expression": f"profile_observation={item.code.value};count={item.count}",
            "affected_fields": [item.field_path] if item.field_path is not None else [],
        }
        for index, item in enumerate(source_profile.data_quality_observations, start=1)
    ]
    selected_entity_by_field = {item.key_field: item.id for item in intent.entities}
    for index, candidate in enumerate(source_profile.candidate_identifiers, start=1):
        entity_id = selected_entity_by_field.get(candidate.field_path)
        rules.append(
            {
                "name": f"candidate_identifier_{index}_{_safe_name(candidate.field_path)}",
                "description": (
                    f"Candidate identifier coverage={candidate.coverage} and "
                    f"uniqueness_ratio={candidate.uniqueness_ratio}"
                    + (f" for declared entity {entity_id}" if entity_id else "")
                ),
                "severity": RuleSeverity.INFO.value,
                "expression": (
                    f"coverage={candidate.coverage};uniqueness_ratio={candidate.uniqueness_ratio}"
                ),
                "affected_fields": [candidate.field_path],
            }
        )
    return rules


def _compile_profile_observations(source_profile: SourceProfile) -> list[dict[str, Any]]:
    return [
        {
            "statement": (
                f"SourceProfile observed {item.code.value} in {item.count} source records"
            ),
            "evidence_field_paths": ([item.field_path] if item.field_path is not None else []),
        }
        for item in source_profile.data_quality_observations
    ]


def _stable_names(paths: list[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    used: set[str] = set()
    for path in paths:
        base = _safe_name(path)
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        names[path] = name
        used.add(name)
    return names


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "field"
