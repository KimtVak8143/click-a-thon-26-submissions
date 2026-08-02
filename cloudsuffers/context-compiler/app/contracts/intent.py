from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.context.models import MetricComputability
from app.contracts.models import (
    EntityRole,
    MetricValueType,
    NormalizationRule,
    NullHandling,
    QuestionSupportClassification,
    RelationshipCardinality,
    SemanticType,
    ZeroDenominatorBehavior,
)
from app.contracts.validator import contains_executable_content, mentioned_in_spec
from app.profiling.models import FieldProfile, JsonType, SourceProfile

_METRIC_CALL_REFERENCE = re.compile(r"\b[a-z][a-z0-9_]*\s*\(\s*([a-z][a-z0-9_.]*)", re.IGNORECASE)
_FUNNEL_SIGNAL = re.compile(
    r"(?:→|->)|\b(?:funnel|journey|sequence|ordered|in order|step-by-step|user actions?|"
    r"event flow|time from)\b",
    re.IGNORECASE,
)
_NON_FUNNEL_SIGNAL = re.compile(
    r"\b(?:no funnel|not a funnel|no ordered (?:journey|sequence|flow)|not ordered|"
    r"unordered|independent events?)\b",
    re.IGNORECASE,
)
_FUNNEL_EXPLANATION = re.compile(
    r"\b(?:funnel|journey|sequence|ordered|independent|single event|cannot link|"
    r"no stable (?:key|identifier))\b",
    re.IGNORECASE,
)
_SEMANTIC_ENTITY_NAME = re.compile(r"^[a-z][a-z_]*$")
_QUANTITATIVE_CLAIM = re.compile(
    r"(?:[$€£¥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:%|bps?\b|x\b|"
    r"milliseconds?\b|ms\b|seconds?\b|minutes?\b|hours?\b|days?\b|weeks?\b|"
    r"(?:USD|EUR|GBP|INR|JPY)\b))",
    re.IGNORECASE,
)
_BOOLEAN_PREDICATE = re.compile(
    r"\b([a-z][a-z0-9_.]*)\s*(?:==|=|\bis\b)\s*(true|false)\b", re.IGNORECASE
)
_BOOLEAN_PREDICATE_NEQ = re.compile(r"\b([a-z][a-z0-9_.]*)\s*!=\s*(true|false)\b", re.IGNORECASE)
_BOOLEAN_PREDICATE_NOT = re.compile(r"\bNOT\s+([a-z][a-z0-9_.]*)\b", re.IGNORECASE)
_BOOLEAN_PREDICATE_BANG = re.compile(r"!([a-z][a-z0-9_.]*)\b")
_DURATION_FIELD = re.compile(
    r"(?:^|[._])(?:latency|duration|elapsed|response_time|processing_time|time_to_[a-z0-9_]+)"
    r"(?:_[a-z]+)?$|(?:_ms|_millis|_milliseconds|_seconds|_secs)$",
    re.IGNORECASE,
)
_WORKFLOW_KEY = re.compile(
    r"^(?:application|group|share|workflow|journey|session|order|checkout|request|case|"
    r"booking|transaction|process|flow)_id$",
    re.IGNORECASE,
)
_PERSON_KEY = re.compile(
    r"^(?:user|person|account|member|customer|traveller|traveler|visitor)_id$", re.IGNORECASE
)
_EVENT_ENTITY_SIGNAL = re.compile(
    r"\b(?:event itself|each event|per event|event as (?:the )?(?:primary |analytical )?entity|"
    r"event is (?:the )?(?:primary |analytical )?entity)\b",
    re.IGNORECASE,
)
_EXISTING_DATA_SIGNAL = re.compile(
    r"\b(?:standard|baseline|control|existing|historical|before|other feature|comparison)\b",
    re.IGNORECASE,
)
_EXTERNAL_CONTEXT_SIGNAL = re.compile(
    r"\b(?:industry|benchmark|competitor|regulation|regulatory|market|external)\b",
    re.IGNORECASE,
)
_FAILURE_SIGNAL = re.compile(
    r"\b(?:fail|fails|failed|failing|failure|error|unsuccessful)\b", re.IGNORECASE
)
_SUCCESS_SIGNAL = re.compile(
    r"\b(?:success|succeeds|successful|conversion|convert|confirmed|completed)\b", re.IGNORECASE
)
_SPEED_SIGNAL = re.compile(
    r"\b(?:faster|speed|latency|duration|elapsed|time[- ]to|how long)\b", re.IGNORECASE
)


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IntentFeature(IntentModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    objective: str = Field(min_length=1)


class IntentEntity(IntentModel):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z_]*$")
    key_field: str = Field(min_length=1)
    role: EntityRole
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def reject_value_like_names(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{8,}", value, re.IGNORECASE):
            raise ValueError("entity id must be a semantic type name, not a hex-like value")
        return value


class IntentFunnel(IntentModel):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    ordered_events: list[str] = Field(min_length=2)
    ordered: bool = True
    description: str | None = None
    workflow_grain: str = Field(min_length=1)
    attribution_window: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        if len(self.ordered_events) != len(set(self.ordered_events)):
            raise ValueError("funnel ordered_events must not repeat an event")
        return self


class IntentMetric(IntentModel):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    numerator: str = Field(min_length=1)
    denominator: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    aggregation_grain: str = Field(min_length=1)
    analysis_window: str = Field(min_length=1)
    zero_denominator_behavior: ZeroDenominatorBehavior
    value_type: MetricValueType
    currency_dimension_field: str | None = None
    fx_normalization_rule: str | None = None
    time_attribution: str = Field(min_length=1)
    deduplication_policy: str = Field(min_length=1)
    dimensions: list[str]
    computability: MetricComputability
    evidence_ids: list[str] = Field(min_length=1)
    duration_start_event: str | None = None
    duration_end_event: str | None = None

    @model_validator(mode="after")
    def validate_currency_safety(self) -> Self:
        if (
            self.value_type == MetricValueType.CURRENCY
            and not self.currency_dimension_field
            and not self.fx_normalization_rule
        ):
            raise ValueError(
                "currency metrics require currency_dimension_field or fx_normalization_rule"
            )
        return self


class IntentDimension(IntentModel):
    field_path: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    null_handling: NullHandling = NullHandling.KEEP
    normalization_rules: list[NormalizationRule] = Field(default_factory=list)
    spec_only: bool = False
    semantic_type: SemanticType | None = None

    @model_validator(mode="after")
    def validate_semantic_type(self) -> Self:
        if self.spec_only and self.semantic_type is None:
            raise ValueError("spec_only dimensions require semantic_type")
        return self


class IntentRelationship(IntentModel):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    source_entity_id: str = Field(min_length=1)
    target_entity_id: str = Field(min_length=1)
    source_field: str = Field(min_length=1)
    target_field: str = Field(min_length=1)
    cardinality: RelationshipCardinality
    temporal_constraint: str | None = None
    description: str = Field(min_length=1)


class IntentObservation(IntentModel):
    statement: str = Field(min_length=1)
    evidence_field_paths: list[str] = Field(default_factory=list)


class IntentAssumption(IntentModel):
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    blocking: bool = False


class IntentOpenQuestion(IntentModel):
    question: str = Field(min_length=1)
    classification: QuestionSupportClassification
    blocking: bool = False
    context: str | None = None
    evidence_ids: list[str] = Field(min_length=1)


class ContractIntent(IntentModel):
    feature: IntentFeature
    entities: list[IntentEntity] = Field(min_length=1)
    primary_entity_id: str = Field(min_length=1)
    funnels: list[IntentFunnel] = Field(default_factory=list)
    metrics: list[IntentMetric] = Field(min_length=1)
    dimensions: list[IntentDimension] = Field(min_length=1)
    relationships: list[IntentRelationship] = Field(default_factory=list)
    observations: list[IntentObservation] = Field(default_factory=list)
    assumptions: list[IntentAssumption] = Field(default_factory=list)
    open_questions: list[IntentOpenQuestion] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_unambiguous_entity_roles(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        primary_entity_id = value.get("primary_entity_id")
        entities = value.get("entities")
        if not isinstance(primary_entity_id, str) or not isinstance(entities, list):
            return value
        matching_indices = [
            index
            for index, entity in enumerate(entities)
            if isinstance(entity, dict) and entity.get("id") == primary_entity_id
        ]
        if len(matching_indices) != 1:
            return value
        primary_index = matching_indices[0]
        normalized_entities = [
            {**entity, "role": "primary" if index == primary_index else "secondary"}
            if isinstance(entity, dict)
            else entity
            for index, entity in enumerate(entities)
        ]
        return {**value, "entities": normalized_entities}

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        _require_unique("entity id", [item.id for item in self.entities])
        _require_unique("entity key field", [item.key_field for item in self.entities])
        _require_unique("funnel id", [item.id for item in self.funnels])
        _require_unique("funnel name", [item.name for item in self.funnels])
        _require_unique("metric id", [item.id for item in self.metrics])
        _require_unique("metric name", [item.name for item in self.metrics])
        _require_unique("dimension field path", [item.field_path for item in self.dimensions])
        _require_unique("relationship id", [item.id for item in self.relationships])

        entity_ids = {item.id for item in self.entities}
        primary_ids = [item.id for item in self.entities if item.role == EntityRole.PRIMARY]
        if len(primary_ids) != 1:
            raise ValueError("exactly one entity must have role='primary'")
        if self.primary_entity_id not in entity_ids:
            raise ValueError(
                "primary_entity_id must reference entities[].id exactly; "
                f"allowed entity ids: {sorted(entity_ids)}"
            )
        if primary_ids != [self.primary_entity_id]:
            raise ValueError("primary_entity_id must reference the entity with role='primary'")

        for index, funnel in enumerate(self.funnels):
            if funnel.entity_id not in entity_ids:
                raise ValueError(
                    f"funnels[{index}].entity_id={funnel.entity_id!r} must reference "
                    f"entities[].id; allowed entity ids: {sorted(entity_ids)}"
                )
        for index, metric in enumerate(self.metrics):
            if metric.entity_id not in entity_ids:
                raise ValueError(
                    f"metrics[{index}].entity_id={metric.entity_id!r} must reference "
                    f"entities[].id; allowed entity ids: {sorted(entity_ids)}"
                )
        for index, relationship in enumerate(self.relationships):
            if (
                relationship.source_entity_id not in entity_ids
                or relationship.target_entity_id not in entity_ids
            ):
                raise ValueError(
                    f"relationships[{index}] source_entity_id and target_entity_id must "
                    f"reference entities[].id; allowed entity ids: {sorted(entity_ids)}"
                )

        observations = {item.statement.casefold() for item in self.observations}
        assumptions = {item.statement.casefold() for item in self.assumptions}
        if observations & assumptions:
            raise ValueError("the same statement cannot be both an observation and assumption")
        return self


@dataclass(frozen=True)
class IntentValidationError:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class SemanticContractRequirements:
    preferred_primary_entity_id: str | None
    preferred_primary_key_field: str | None
    ordered_event_names: tuple[str, ...]
    requested_dimension_paths: tuple[str, ...]
    duration_field_paths: tuple[str, ...]
    boolean_predicate_fields: tuple[str, ...]
    requested_numeric_paths: tuple[str, ...]
    multi_currency_paths: tuple[str, ...]
    unsupported_question_classifications: tuple[str, ...]
    conversion_metric_required: bool
    duration_metric_required: bool
    failure_metric_required: bool
    currency_metric_required: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "preferred_primary_entity": (
                {
                    "id": self.preferred_primary_entity_id,
                    "key_field": self.preferred_primary_key_field,
                }
                if self.preferred_primary_entity_id and self.preferred_primary_key_field
                else None
            ),
            "ordered_event_names": list(self.ordered_event_names),
            "requested_dimension_paths": list(self.requested_dimension_paths),
            "duration_field_paths": list(self.duration_field_paths),
            "boolean_predicate_fields": list(self.boolean_predicate_fields),
            "requested_numeric_paths": list(self.requested_numeric_paths),
            "multi_currency_paths": list(self.multi_currency_paths),
            "unsupported_question_classifications": list(self.unsupported_question_classifications),
            "conversion_metric_required": self.conversion_metric_required,
            "duration_metric_required": self.duration_metric_required,
            "failure_metric_required": self.failure_metric_required,
            "currency_metric_required": self.currency_metric_required,
        }


def canonical_entity_name_for_key(field_path: str, feature_spec: str = "") -> str | None:
    """Derive a type-level entity name without inspecting source values."""

    leaf = field_path.rsplit(".", 1)[-1].casefold()
    if leaf == "id":
        return "event" if _EVENT_ENTITY_SIGNAL.search(feature_spec) else None
    for suffix in ("_id", "_key", "_identifier"):
        if leaf.endswith(suffix) and len(leaf) > len(suffix):
            leaf = leaf[: -len(suffix)]
            break
    return leaf if _SEMANTIC_ENTITY_NAME.fullmatch(leaf) else None


def specification_allows_event_entity(feature_spec: str) -> bool:
    return _EVENT_ENTITY_SIGNAL.search(feature_spec) is not None


def ordered_events_from_spec(
    feature_spec: str, observed_events: frozenset[str] | set[str]
) -> tuple[str, ...]:
    folded = feature_spec.casefold()
    positioned = [
        (folded.find(event.casefold()), event)
        for event in observed_events
        if event.casefold() in folded
    ]
    return tuple(event for position, event in sorted(positioned) if position >= 0)


def preferred_primary_entity_key(
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    required_events: tuple[str, ...] | None = None,
) -> str | None:
    """Prefer a stable workflow key and use a person key only as a fallback."""

    events = set(required_events or ())
    fields = {item.path: item for item in source_profile.fields}
    title = _feature_title_text(feature_spec)
    eligible: list[tuple[int, int, int, float, float, str]] = []
    for candidate in source_profile.candidate_identifiers:
        canonical_name = canonical_entity_name_for_key(candidate.field_path, feature_spec)
        field = fields.get(candidate.field_path)
        if canonical_name is None or field is None or candidate.uniqueness_ratio >= 1:
            continue
        if events and not events <= set(field.observed_in_events):
            continue
        leaf = candidate.field_path.rsplit(".", 1)[-1]
        scope_rank = 0 if _WORKFLOW_KEY.fullmatch(leaf) else 2 if _PERSON_KEY.fullmatch(leaf) else 1
        title_position = _normalized_words(title).find(_normalized_words(canonical_name))
        title_rank = title_position if title_position >= 0 else 1_000_000
        mentioned_rank = 0 if mentioned_in_spec(feature_spec, candidate.field_path) else 1
        eligible.append(
            (
                scope_rank,
                title_rank,
                mentioned_rank,
                -candidate.coverage,
                candidate.uniqueness_ratio,
                candidate.field_path,
            )
        )
    return min(eligible)[-1] if eligible else None


def semantic_contract_requirements(
    feature_spec: str, source_profile: SourceProfile
) -> SemanticContractRequirements:
    ordered_events = ordered_events_from_spec(feature_spec, source_profile.event_names)
    funnel_required = specification_requires_funnel(feature_spec, source_profile.event_names)
    if not funnel_required:
        ordered_events = ()
    preferred_key = preferred_primary_entity_key(
        source_profile, feature_spec, required_events=ordered_events
    )
    preferred_name = (
        canonical_entity_name_for_key(preferred_key, feature_spec) if preferred_key else None
    )
    identifier_paths = {item.field_path for item in source_profile.candidate_identifiers}
    question_text = _pm_question_text(feature_spec)
    requested_dimensions = tuple(
        sorted(
            field.path
            for field in source_profile.fields
            if _field_is_requested(question_text, field.path)
            and infer_observed_semantic_type(field, is_identifier=field.path in identifier_paths)
            in {SemanticType.STRING, SemanticType.COUNTRY_CODE}
        )
    )
    duration_fields = tuple(
        sorted(
            field.path
            for field in source_profile.fields
            if _DURATION_FIELD.search(field.path)
            and set(field.observed_types) & {JsonType.INTEGER, JsonType.NUMBER}
        )
    )
    boolean_fields = tuple(
        sorted(
            field.path
            for field in source_profile.fields
            if set(field.observed_types) - {JsonType.NULL} == {JsonType.BOOLEAN}
            and _field_is_requested(question_text, field.path)
        )
    )
    requested_numeric_paths = tuple(
        sorted(
            field.path
            for field in source_profile.fields
            if _field_is_requested(question_text, field.path)
            and set(field.observed_types) - {JsonType.NULL}
            and set(field.observed_types) - {JsonType.NULL} <= {JsonType.INTEGER, JsonType.NUMBER}
        )
    )
    multi_currency_paths = tuple(
        sorted(
            field.field_path
            for field in source_profile.currency_fields
            if field.distinct_count > 1 and _field_is_requested(question_text, field.field_path)
        )
    )
    unsupported_question_classifications = tuple(
        sorted(
            classification.value
            for _, classification in unsupported_pm_questions(feature_spec, source_profile)
        )
    )
    speed_requested = _SPEED_SIGNAL.search(question_text) is not None
    failure_requested = _FAILURE_SIGNAL.search(question_text) is not None
    return SemanticContractRequirements(
        preferred_primary_entity_id=preferred_name,
        preferred_primary_key_field=preferred_key,
        ordered_event_names=ordered_events,
        requested_dimension_paths=requested_dimensions,
        duration_field_paths=duration_fields,
        boolean_predicate_fields=boolean_fields,
        requested_numeric_paths=requested_numeric_paths,
        multi_currency_paths=multi_currency_paths,
        unsupported_question_classifications=unsupported_question_classifications,
        conversion_metric_required=funnel_required,
        duration_metric_required=speed_requested and bool(duration_fields),
        failure_metric_required=failure_requested and bool(boolean_fields),
        currency_metric_required=bool(requested_numeric_paths and multi_currency_paths),
    )


def validate_intent_grounding(
    intent: ContractIntent,
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    expected_feature_slug: str,
    context_summary: str | None = None,
    context_evidence_ids: set[str] | None = None,
) -> list[IntentValidationError]:
    errors: list[IntentValidationError] = []
    fields = {item.path: item for item in source_profile.fields}
    events = source_profile.event_names
    identifiers = {item.field_path: item for item in source_profile.candidate_identifiers}
    entity_ids = {item.id for item in intent.entities}
    declared_fields = fields.keys() | {
        item.field_path for item in intent.dimensions if item.spec_only
    }
    requirements = semantic_contract_requirements(feature_spec, source_profile)
    allowed_evidence_ids = {
        "feature_specification",
        "source_profile",
        *(context_evidence_ids or set()),
    }

    if intent.feature.slug != expected_feature_slug:
        errors.append(
            IntentValidationError(
                "feature_slug_mismatch",
                "feature.slug",
                f"feature slug must be {expected_feature_slug!r}",
            )
        )

    funnel_required = specification_requires_funnel(feature_spec, events)
    if not intent.funnels and funnel_required:
        errors.append(
            IntentValidationError(
                "missing_required_funnel",
                "funnels",
                "the specification describes an ordered multi-event journey, so at least one "
                "grounded funnel is required",
            )
        )
    elif not intent.funnels and not _has_non_funnel_explanation(intent):
        errors.append(
            IntentValidationError(
                "missing_funnel_explanation",
                "funnels",
                "an empty funnel list requires an observation or open question explaining why "
                "the feature is not an ordered event journey",
            )
        )

    for index, entity in enumerate(intent.entities):
        canonical_name = canonical_entity_name_for_key(entity.key_field, feature_spec)
        event_identity_field = (
            source_profile.duplicate_event_id.field_path
            if source_profile.duplicate_event_id is not None
            else None
        )
        if entity.key_field == event_identity_field and not specification_allows_event_entity(
            feature_spec
        ):
            errors.append(
                IntentValidationError(
                    "event_identity_entity_key",
                    f"entities.{index}.key_field",
                    "the SourceProfile-identified event identity field is row identity, not a "
                    "business entity key; the specification must explicitly select events as "
                    "the analytical entity",
                )
            )
        if entity.key_field == "id" and canonical_name is None:
            errors.append(
                IntentValidationError(
                    "generic_event_id_entity_key",
                    f"entities.{index}.key_field",
                    "generic event-envelope field 'id' is event identity, not a business "
                    "entity key; it is allowed only when the specification explicitly defines "
                    "the event itself as the analytical entity",
                )
            )

        elif (
            entity.key_field in fields
            and entity.key_field.casefold().endswith("_id")
            and entity.id != canonical_name
        ):
            errors.append(
                IntentValidationError(
                    "non_canonical_entity_name",
                    f"entities.{index}.id",
                    f"entity id must be the canonical type name {canonical_name!r} derived "
                    f"from key_field {entity.key_field!r}",
                )
            )
        errors.extend(
            _validate_evidence_ids(
                entity.evidence_ids,
                allowed_evidence_ids,
                f"entities.{index}.evidence_ids",
            )
        )
        if entity.key_field not in fields:
            errors.append(
                IntentValidationError(
                    "invented_entity_key",
                    f"entities.{index}.key_field",
                    f"entity key field {entity.key_field!r} is not observed; allowed field "
                    f"paths: {sorted(fields)}",
                )
            )
        elif entity.key_field not in identifiers:
            errors.append(
                IntentValidationError(
                    "unstable_entity_key",
                    f"entities.{index}.key_field",
                    f"entity key field {entity.key_field!r} is not a candidate identifier",
                )
            )
        elif any(
            isinstance(example, str) and example.casefold() == entity.id.casefold()
            for example in fields[entity.key_field].examples
        ):
            errors.append(
                IntentValidationError(
                    "entity_name_from_example_value",
                    f"entities.{index}.id",
                    "entity ids must describe entity types and must not copy a source value",
                )
            )

    primary_entity = next(
        (item for item in intent.entities if item.id == intent.primary_entity_id), None
    )
    if (
        primary_entity is not None
        and primary_entity.key_field in identifiers
        and requirements.preferred_primary_key_field is not None
        and primary_entity.key_field != requirements.preferred_primary_key_field
    ):
        errors.append(
            IntentValidationError(
                "unsafe_primary_entity_key",
                "primary_entity_id",
                "primary entity must use the narrowest stable journey key "
                f"{requirements.preferred_primary_key_field!r}; a person-level key is only a "
                "fallback when no narrower workflow key can link the journey",
            )
        )

    for index, funnel in enumerate(intent.funnels):
        unknown_events = sorted(set(funnel.ordered_events) - events)
        if unknown_events:
            errors.append(
                IntentValidationError(
                    "unknown_funnel_event",
                    f"funnels.{index}.ordered_events",
                    f"funnel steps must reference observed events; unknown: {unknown_events}; "
                    f"allowed events: {sorted(events)}",
                )
            )
            continue
        entity = next(item for item in intent.entities if item.id == funnel.entity_id)
        if funnel.workflow_grain != entity.id:
            errors.append(
                IntentValidationError(
                    "inconsistent_funnel_grain",
                    f"funnels.{index}.workflow_grain",
                    "funnel workflow_grain must equal its declared semantic entity id",
                )
            )
        errors.extend(
            _validate_evidence_ids(
                funnel.evidence_ids,
                allowed_evidence_ids,
                f"funnels.{index}.evidence_ids",
            )
        )
        if funnel.entity_id != intent.primary_entity_id and not _entities_are_related(
            intent, funnel.entity_id, intent.primary_entity_id
        ):
            errors.append(
                IntentValidationError(
                    "inconsistent_funnel_entity",
                    f"funnels.{index}.entity_id",
                    "funnel entity must match primary_entity_id unless a declared relationship "
                    "connects the two entities",
                )
            )
        field = fields.get(entity.key_field)
        candidate = identifiers.get(entity.key_field)
        if field is None or candidate is None:
            continue
        if candidate.uniqueness_ratio >= 1:
            errors.append(
                IntentValidationError(
                    "unstable_funnel_entity",
                    f"funnels.{index}.entity_id",
                    f"entity {entity.id!r} key is unique per row and cannot link funnel steps",
                )
            )
        missing = sorted(set(funnel.ordered_events) - set(field.observed_in_events))
        if missing:
            errors.append(
                IntentValidationError(
                    "unstable_funnel_entity",
                    f"funnels.{index}.entity_id",
                    f"entity {entity.id!r} key is not observed in every funnel event: {missing}",
                )
            )
        if requirements.ordered_event_names and not _contains_ordered_subsequence(
            funnel.ordered_events, requirements.ordered_event_names
        ):
            errors.append(
                IntentValidationError(
                    "incomplete_spec_funnel",
                    f"funnels.{index}.ordered_events",
                    "funnel must include every observed ordered event named by the "
                    f"specification in order: {list(requirements.ordered_event_names)}",
                )
            )

    for index, dimension in enumerate(intent.dimensions):
        observed = dimension.field_path in fields
        if dimension.spec_only and observed:
            errors.append(
                IntentValidationError(
                    "observed_marked_spec_only",
                    f"dimensions.{index}.spec_only",
                    f"observed field {dimension.field_path!r} must not be spec_only",
                )
            )
        elif not dimension.spec_only and not observed:
            errors.append(
                IntentValidationError(
                    "invented_observed_field",
                    f"dimensions.{index}.field_path",
                    f"field {dimension.field_path!r} is not observed; mark it spec_only only "
                    "when explicitly named by the specification",
                )
            )
        elif dimension.spec_only and not mentioned_in_spec(feature_spec, dimension.field_path):
            errors.append(
                IntentValidationError(
                    "invented_spec_only_field",
                    f"dimensions.{index}.field_path",
                    f"spec_only field {dimension.field_path!r} is not named by the specification",
                )
            )
        elif observed and dimension.semantic_type is not None:
            expected_semantic_type = infer_observed_semantic_type(
                fields[dimension.field_path],
                is_identifier=dimension.field_path in identifiers,
            )
            if dimension.semantic_type != expected_semantic_type:
                errors.append(
                    IntentValidationError(
                        "observed_dimension_semantic_type_mismatch",
                        f"dimensions.{index}.semantic_type",
                        "observed dimension semantic_type must match the deterministic "
                        f"SourceProfile-derived value {expected_semantic_type.value!r}",
                    )
                )

    dimension_paths = {item.field_path for item in intent.dimensions}
    for index, metric in enumerate(intent.metrics):
        errors.extend(
            _validate_evidence_ids(
                metric.evidence_ids,
                allowed_evidence_ids,
                f"metrics.{index}.evidence_ids",
            )
        )
        if metric.id == "conversion_rate":
            errors.append(
                IntentValidationError(
                    "ambiguous_conversion_metric",
                    f"metrics.{index}.id",
                    "conversion_rate is ambiguous; the metric id must identify its denominator",
                )
            )
        unknown_metric_dimensions = sorted(set(metric.dimensions) - dimension_paths)
        if unknown_metric_dimensions:
            errors.append(
                IntentValidationError(
                    "unknown_metric_dimension",
                    f"metrics.{index}.dimensions",
                    "metric dimensions must reference dimensions[].field_path exactly; "
                    f"unknown: {unknown_metric_dimensions}",
                )
            )
        if metric.entity_id != intent.primary_entity_id and not _entities_are_related(
            intent, metric.entity_id, intent.primary_entity_id
        ):
            errors.append(
                IntentValidationError(
                    "inconsistent_metric_entity",
                    f"metrics.{index}.entity_id",
                    "metric entity must match primary_entity_id unless a declared relationship "
                    "connects the two entities",
                )
            )
        if (
            metric.currency_dimension_field is not None
            and metric.currency_dimension_field not in dimension_paths
        ):
            errors.append(
                IntentValidationError(
                    "unknown_currency_dimension",
                    f"metrics.{index}.currency_dimension_field",
                    "currency_dimension_field must reference dimensions[].field_path exactly; "
                    f"allowed dimension field paths: {sorted(dimension_paths)}",
                )
            )
        allowed_metric_references = (
            events | set(fields) | entity_ids | {item.key_field for item in intent.entities}
        )
        for expression_name, expression in (
            ("numerator", metric.numerator),
            ("denominator", metric.denominator),
        ):
            # Accept event-qualified field references: event.field → bare field must be allowed.
            unknown_references = sorted(
                {
                    match.group(1)
                    for match in _METRIC_CALL_REFERENCE.finditer(expression)
                    if match.group(1) not in allowed_metric_references
                    and _bare_name(match.group(1)) not in allowed_metric_references
                }
            )
            if unknown_references:
                errors.append(
                    IntentValidationError(
                        "invented_metric_reference",
                        f"metrics.{index}.{expression_name}",
                        "metric expressions must reference observed events, observed fields, or "
                        f"declared entity IDs; unknown: {unknown_references}",
                    )
                )
        errors.extend(
            _validate_metric_semantics(
                metric,
                index=index,
                fields=fields,
                events=events,
                requirements=requirements,
            )
        )

    errors.extend(_validate_required_analytical_coverage(intent, requirements))

    for index, relationship in enumerate(intent.relationships):
        for field_name, value in (
            ("source_field", relationship.source_field),
            ("target_field", relationship.target_field),
        ):
            if value not in declared_fields:
                errors.append(
                    IntentValidationError(
                        "invented_relationship_field",
                        f"relationships.{index}.{field_name}",
                        f"relationship field {value!r} is not observed or declared spec_only",
                    )
                )

    for index, observation in enumerate(intent.observations):
        unknown = sorted(set(observation.evidence_field_paths) - declared_fields)
        if unknown:
            errors.append(
                IntentValidationError(
                    "invented_observation_field",
                    f"observations.{index}.evidence_field_paths",
                    f"observation evidence contains unknown field paths: {unknown}",
                )
            )

    quantitative_evidence = "\n".join(
        item for item in (feature_spec, context_summary or "") if item
    ).casefold()
    for index, assumption in enumerate(intent.assumptions):
        unsupported_claims = sorted(
            {
                match.group(0)
                for match in _QUANTITATIVE_CLAIM.finditer(assumption.statement)
                if match.group(0).casefold() not in quantitative_evidence
            }
        )
        if unsupported_claims:
            errors.append(
                IntentValidationError(
                    "unsupported_quantified_assumption",
                    f"assumptions.{index}.statement",
                    "quantitative assumptions require an exact claim from the feature "
                    f"specification, supplied context, or computed evidence: {unsupported_claims}",
                )
            )

    for index, question in enumerate(intent.open_questions):
        errors.extend(
            _validate_evidence_ids(
                question.evidence_ids,
                allowed_evidence_ids,
                f"open_questions.{index}.evidence_ids",
            )
        )
        expected_classification = classify_question_support(question.question, source_profile)
        if question.classification != expected_classification:
            errors.append(
                IntentValidationError(
                    "incorrect_question_classification",
                    f"open_questions.{index}.classification",
                    "question support classification must be derived from available fields, "
                    f"events, and context requirements; expected {expected_classification.value!r}",
                )
            )
        if (
            question.classification == QuestionSupportClassification.COMPUTABLE_FROM_FEATURE
            and question.blocking
        ):
            errors.append(
                IntentValidationError(
                    "computable_question_marked_blocking",
                    f"open_questions.{index}.blocking",
                    "a question computable from observed feature data must not be marked blocking",
                )
            )

    errors.extend(_validate_intent_text(intent))
    if intent.primary_entity_id not in entity_ids:
        # Normally caught by Pydantic. This guard keeps the grounding function total.
        errors.append(
            IntentValidationError(
                "unknown_primary_entity",
                "primary_entity_id",
                f"primary_entity_id must reference entities[].id: {sorted(entity_ids)}",
            )
        )
    return errors


def specification_requires_funnel(
    feature_spec: str, observed_events: frozenset[str] | set[str]
) -> bool:
    folded = feature_spec.casefold()
    mentioned_events = [event for event in observed_events if event.casefold() in folded]
    if len(mentioned_events) < 2 or _NON_FUNNEL_SIGNAL.search(feature_spec):
        return False
    return _FUNNEL_SIGNAL.search(feature_spec) is not None


def classify_question_support(
    question: str, source_profile: SourceProfile
) -> QuestionSupportClassification:
    if _EXTERNAL_CONTEXT_SIGNAL.search(question):
        return QuestionSupportClassification.REQUIRES_EXTERNAL_CONTEXT
    if _EXISTING_DATA_SIGNAL.search(question):
        return QuestionSupportClassification.REQUIRES_EXISTING_TABLES
    folded = _normalized_words(question)
    observed_names = source_profile.event_names | source_profile.field_paths
    if any(_normalized_words(name) in folded for name in observed_names):
        return QuestionSupportClassification.COMPUTABLE_FROM_FEATURE
    return QuestionSupportClassification.NOT_COMPUTABLE


def unsupported_pm_questions(
    feature_spec: str, source_profile: SourceProfile
) -> tuple[tuple[str, QuestionSupportClassification], ...]:
    """Return unsupported PM questions with deterministic support classifications."""

    unsupported = {
        QuestionSupportClassification.REQUIRES_EXTERNAL_CONTEXT,
        QuestionSupportClassification.NOT_COMPUTABLE,
    }
    return tuple(
        (question, classification)
        for question in _pm_questions(feature_spec)
        if (classification := classify_question_support(question, source_profile)) in unsupported
    )


def _validate_metric_semantics(
    metric: IntentMetric,
    *,
    index: int,
    fields: dict[str, FieldProfile],
    events: frozenset[str],
    requirements: SemanticContractRequirements,
) -> list[IntentValidationError]:
    errors: list[IntentValidationError] = []
    prefix = f"metrics.{index}"
    observed_boolean_fields = {
        path
        for path, field in fields.items()
        if set(field.observed_types) - {JsonType.NULL} == {JsonType.BOOLEAN}
    }
    observed_numeric_fields = {
        path
        for path, field in fields.items()
        if set(field.observed_types) - {JsonType.NULL} <= {JsonType.INTEGER, JsonType.NUMBER}
        and set(field.observed_types) - {JsonType.NULL}
    }

    for operand_name, expression in (
        ("numerator", metric.numerator),
        ("denominator", metric.denominator),
    ):
        if metric.value_type == MetricValueType.RATIO and not _operand_is_grounded(
            expression, events=events, fields=set(fields), boolean_fields=observed_boolean_fields
        ):
            errors.append(
                IntentValidationError(
                    "ungrounded_ratio_operand",
                    f"{prefix}.{operand_name}",
                    "ratio operands must reference an observed event, observed boolean "
                    "predicate, observed field, or declared metric expression",
                )
            )

    semantic_text = f"{metric.name} {metric.description}"
    numerator_events = _referenced_events(metric.numerator, events)
    boolean_predicates = _valid_boolean_predicates(metric.numerator, observed_boolean_fields)
    if _FAILURE_SIGNAL.search(semantic_text):
        has_false_predicate = any(value == "false" for _, value in boolean_predicates)
        has_failure_event = any(
            _FAILURE_SIGNAL.search(_normalized_words(event)) for event in numerator_events
        )
        if not has_false_predicate and not has_failure_event:
            errors.append(
                IntentValidationError(
                    "ungrounded_failure_metric",
                    f"{prefix}.numerator",
                    "a failure metric numerator must use an observed false boolean predicate "
                    "or an observed event explicitly representing failure",
                )
            )
    elif (
        len(events) > 1
        and _SUCCESS_SIGNAL.search(semantic_text)
        and metric.value_type == MetricValueType.RATIO
    ):
        has_true_predicate = any(value == "true" for _, value in boolean_predicates)
        required_end_event = (
            requirements.ordered_event_names[-1] if requirements.ordered_event_names else None
        )
        has_success_event = any(
            _SUCCESS_SIGNAL.search(_normalized_words(event))
            or (required_end_event is not None and event == required_end_event)
            for event in numerator_events
        )
        if not has_true_predicate and not has_success_event:
            errors.append(
                IntentValidationError(
                    "ungrounded_success_metric",
                    f"{prefix}.numerator",
                    "a success metric numerator must use an observed true boolean predicate "
                    "or an observed event explicitly representing success",
                )
            )

    if metric.value_type == MetricValueType.DURATION:
        endpoints = {metric.duration_start_event, metric.duration_end_event}
        duration_fields = {path for path in observed_numeric_fields if _DURATION_FIELD.search(path)}
        has_duration_operand = any(
            _references_name(metric.numerator, path) for path in duration_fields
        ) or _is_timestamp_difference(metric.numerator, events=events)
        endpoints_valid = (
            None not in endpoints
            and len(endpoints) == 2
            and endpoints <= events
            and bool(metric.time_attribution.strip())
            and has_duration_operand
        )
        if not endpoints_valid:
            errors.append(
                IntentValidationError(
                    "ungrounded_duration_metric",
                    prefix,
                    "duration metrics require distinct observed start/end events and a "
                    "deterministic time-attribution rule; event-count ratios or a duration-like "
                    "field alone are insufficient",
                )
            )
    elif metric.value_type in {MetricValueType.NUMBER, MetricValueType.CURRENCY}:
        if not any(_references_name(metric.numerator, path) for path in observed_numeric_fields):
            errors.append(
                IntentValidationError(
                    "ungrounded_value_metric",
                    f"{prefix}.numerator",
                    "number, average, and currency metrics require an observed numeric field",
                )
            )

    if requirements.failure_metric_required and _FAILURE_SIGNAL.search(semantic_text):
        required_predicates = set(requirements.boolean_predicate_fields)
        if required_predicates and not any(
            field in required_predicates and value == "false" for field, value in boolean_predicates
        ):
            errors.append(
                IntentValidationError(
                    "missing_requested_failure_predicate",
                    f"{prefix}.numerator",
                    "the specification asks about failures on an observed boolean field; use "
                    f"a false predicate on one of {sorted(required_predicates)}",
                )
            )
    if metric.id == "on_time_delivery_rate":
        fulfilment_fields = {
            path
            for path in fields
            if any(token in path.casefold() for token in ("issued", "issuance", "delivered"))
        }
        if not fulfilment_fields and metric.computability != MetricComputability.EXTERNAL:
            errors.append(
                IntentValidationError(
                    "unavailable_on_time_metric",
                    f"{prefix}.computability",
                    "on_time_delivery_rate must be external when issuance evidence is absent",
                )
            )
    return errors


def _validate_evidence_ids(
    evidence_ids: list[str], allowed: set[str], path: str
) -> list[IntentValidationError]:
    unknown = sorted(set(evidence_ids) - allowed)
    if not unknown:
        return []
    return [
        IntentValidationError(
            "unknown_evidence_id",
            path,
            f"evidence references must use supplied evidence IDs; unknown: {unknown}",
        )
    ]


def _validate_required_analytical_coverage(
    intent: ContractIntent, requirements: SemanticContractRequirements
) -> list[IntentValidationError]:
    errors: list[IntentValidationError] = []
    if requirements.conversion_metric_required and requirements.ordered_event_names:
        start_event = requirements.ordered_event_names[0]
        end_event = requirements.ordered_event_names[-1]
        conversion_covered = any(
            metric.value_type == MetricValueType.RATIO
            and _references_name(metric.denominator, start_event)
            and _references_name(metric.numerator, end_event)
            for metric in intent.metrics
        )
        if not conversion_covered:
            errors.append(
                IntentValidationError(
                    "missing_conversion_metric",
                    "metrics",
                    "an ordered-event feature requires a conversion metric from the first "
                    "specified event to the last specified event",
                )
            )
    if requirements.duration_metric_required and not any(
        metric.value_type == MetricValueType.DURATION
        and any(
            _references_name(metric.numerator, field) for field in requirements.duration_field_paths
        )
        for metric in intent.metrics
    ):
        errors.append(
            IntentValidationError(
                "missing_requested_duration_metric",
                "metrics",
                "the specification asks about speed and an observed duration field is "
                f"available: {list(requirements.duration_field_paths)}",
            )
        )
    if requirements.failure_metric_required and not any(
        any(
            field in requirements.boolean_predicate_fields and value == "false"
            for field, value in _valid_boolean_predicates(
                metric.numerator, set(requirements.boolean_predicate_fields)
            )
        )
        for metric in intent.metrics
    ):
        errors.append(
            IntentValidationError(
                "missing_requested_failure_metric",
                "metrics",
                "the specification asks about failures and observed boolean fields support a "
                f"grounded false predicate: {list(requirements.boolean_predicate_fields)}",
            )
        )
    if requirements.currency_metric_required and not any(
        metric.value_type == MetricValueType.CURRENCY
        and any(
            _references_name(metric.numerator, field)
            for field in requirements.requested_numeric_paths
        )
        and (
            metric.currency_dimension_field in requirements.multi_currency_paths
            or bool(metric.fx_normalization_rule)
        )
        for metric in intent.metrics
    ):
        errors.append(
            IntentValidationError(
                "missing_requested_currency_metric",
                "metrics",
                "the specification asks for a monetary metric over observed numeric fields "
                f"{list(requirements.requested_numeric_paths)} with multiple observed currencies "
                f"{list(requirements.multi_currency_paths)}; add a currency metric grouped by "
                "currency or with an explicit FX-normalization rule",
            )
        )
    actual_question_classifications = [
        question.classification.value for question in intent.open_questions
    ]
    missing_question_classifications = list(requirements.unsupported_question_classifications)
    for classification in actual_question_classifications:
        if classification in missing_question_classifications:
            missing_question_classifications.remove(classification)
    if missing_question_classifications:
        errors.append(
            IntentValidationError(
                "missing_unsupported_open_question",
                "open_questions",
                "unsupported PM questions must be represented as open_questions with these "
                f"deterministically derived classifications: {missing_question_classifications}",
            )
        )
    dimension_paths = {item.field_path for item in intent.dimensions}
    missing_dimensions = sorted(set(requirements.requested_dimension_paths) - dimension_paths)
    if missing_dimensions:
        errors.append(
            IntentValidationError(
                "missing_requested_dimension",
                "dimensions",
                "observed fields referenced by PM questions must be included as dimensions: "
                f"{missing_dimensions}",
            )
        )
    return errors


def _bare_name(token: str) -> str:
    """Return the leaf segment after the last dot, or the token itself."""
    return token.rsplit(".", 1)[-1]


def _operand_is_grounded(
    expression: str,
    *,
    events: frozenset[str],
    fields: set[str],
    boolean_fields: set[str],
) -> bool:
    return (
        expression.strip() == "1"
        or bool(_referenced_events(expression, events))
        or any(_references_name(expression, field) for field in fields)
        or bool(_valid_boolean_predicates(expression, boolean_fields))
    )


def _valid_boolean_predicates(expression: str, boolean_fields: set[str]) -> set[tuple[str, str]]:
    # Accept event-qualified references: otp_entered.otp_success matches otp_success.
    predicates = {
        (_bare_name(match.group(1)), match.group(2).casefold())
        for match in _BOOLEAN_PREDICATE.finditer(expression)
        if _bare_name(match.group(1)) in boolean_fields
    }
    predicates.update(
        (_bare_name(match.group(1)), "false" if match.group(2).casefold() == "true" else "true")
        for match in _BOOLEAN_PREDICATE_NEQ.finditer(expression)
        if _bare_name(match.group(1)) in boolean_fields
    )
    predicates.update(
        (_bare_name(match.group(1)), "false")
        for pattern in (_BOOLEAN_PREDICATE_NOT, _BOOLEAN_PREDICATE_BANG)
        for match in pattern.finditer(expression)
        if _bare_name(match.group(1)) in boolean_fields
    )
    return predicates


def _referenced_events(expression: str, events: frozenset[str]) -> set[str]:
    return {event for event in events if _references_name(expression, event)}


def _references_name(expression: str, name: str) -> bool:
    # Remove dot from the negative lookbehind so event.field also matches field.
    # The lookahead keeps dot to prevent partial suffix matches (event.field_extra).
    return bool(re.search(rf"(?<![a-zA-Z0-9_]){re.escape(name)}(?![a-zA-Z0-9_.])", expression))


def _is_timestamp_difference(expression: str, *, events: frozenset[str]) -> bool:
    lowered = expression.casefold()
    has_difference_signal = any(
        signal in lowered
        for signal in ("timestamp_diff", "timestamp difference", "time between", "elapsed")
    )
    return has_difference_signal and len(_referenced_events(expression, events)) >= 2


def _entities_are_related(intent: ContractIntent, left: str, right: str) -> bool:
    return any(
        {relationship.source_entity_id, relationship.target_entity_id} == {left, right}
        for relationship in intent.relationships
    )


def _contains_ordered_subsequence(values: list[str], required: tuple[str, ...]) -> bool:
    iterator = iter(values)
    return all(any(value == expected for value in iterator) for expected in required)


def _pm_question_text(feature_spec: str) -> str:
    lines = feature_spec.splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^\s*#{1,6}\s+.*\b(?:questions?|analysis)\b", line, re.IGNORECASE)
        ),
        None,
    )
    if start is not None:
        return "\n".join(lines[start + 1 :])
    return "\n".join(line for line in lines if "?" in line)


def _pm_questions(feature_spec: str) -> tuple[str, ...]:
    return tuple(
        re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
        for line in _pm_question_text(feature_spec).splitlines()
        if "?" in line
    )


def _feature_title_text(feature_spec: str) -> str:
    for line in feature_spec.splitlines():
        match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if match:
            return match.group(1)
    return ""


def _field_is_requested(text: str, field_path: str) -> bool:
    return _normalized_words(field_path) in _normalized_words(text)


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def infer_observed_semantic_type(field: FieldProfile, *, is_identifier: bool) -> SemanticType:
    if is_identifier:
        return SemanticType.IDENTIFIER
    leaf = field.path.rsplit(".", 1)[-1].removesuffix("[]").casefold()
    if leaf in {"event_time", "timestamp", "created_at", "updated_at"} or leaf.endswith(
        ("_time", "_at")
    ):
        return SemanticType.DATETIME
    if leaf in {
        "country",
        "country_code",
        "destination_code",
        "destination_country_code",
        "geoip_country_code",
    }:
        return SemanticType.COUNTRY_CODE
    observed = set(field.observed_types) - {JsonType.NULL}
    if observed == {JsonType.BOOLEAN}:
        return SemanticType.BOOLEAN
    if observed == {JsonType.INTEGER}:
        return SemanticType.INTEGER
    if observed and observed <= {JsonType.INTEGER, JsonType.NUMBER}:
        return SemanticType.NUMBER
    if observed == {JsonType.ARRAY}:
        return SemanticType.ARRAY
    if observed == {JsonType.OBJECT}:
        return SemanticType.OBJECT
    return SemanticType.STRING


def _has_non_funnel_explanation(intent: ContractIntent) -> bool:
    explanations = [item.statement for item in intent.observations]
    explanations.extend(item.question for item in intent.open_questions)
    explanations.extend(item.context for item in intent.open_questions if item.context is not None)
    return any(_FUNNEL_EXPLANATION.search(item) for item in explanations)


def _validate_intent_text(intent: ContractIntent) -> list[IntentValidationError]:
    errors: list[IntentValidationError] = []
    for path, value in _walk_strings(intent.model_dump(mode="json")):
        if contains_executable_content(value):
            errors.append(
                IntentValidationError(
                    "executable_content",
                    path,
                    "semantic intent must not contain SQL, code fences, or executable instructions",
                )
            )
    return errors


def _walk_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _walk_strings(child, f"{path}.{index}")
        ]
    if isinstance(value, dict):
        return [
            item for key, child in value.items() for item in _walk_strings(child, f"{path}.{key}")
        ]
    return []


def _require_unique(label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label}s must be unique")
