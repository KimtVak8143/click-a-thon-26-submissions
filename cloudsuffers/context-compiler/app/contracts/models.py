import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.context.models import MetricComputability
from app.profiling.models import Sha256, SourceProfile

_CONTRACT_VERSION = re.compile(r"^1\.0$")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TimeWindow(ContractModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed window timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.start > self.end:
            raise ValueError("observed window start must not be after end")
        return self


class FeatureMetadata(ContractModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    objective: str = Field(min_length=1)


class SourceMetadata(ContractModel):
    spec_sha256: Sha256
    events_sha256: Sha256
    row_count: int = Field(ge=0)
    observed_window: TimeWindow


class EntityRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class EntityDefinition(ContractModel):
    name: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    description: str = Field(min_length=1)
    role: EntityRole
    stable: bool
    evidence_ids: list[str] = Field(default_factory=list)


class EventDefinition(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    entity_keys: list[str] = Field(default_factory=list)
    spec_only: bool = False


class SemanticType(StrEnum):
    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    DATETIME = "datetime"
    DATE = "date"
    IDENTIFIER = "identifier"
    COUNTRY_CODE = "country_code"
    ARRAY = "array"
    OBJECT = "object"


class FieldDefinition(ContractModel):
    name: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    semantic_type: SemanticType
    clickhouse_type: str = Field(min_length=1)
    observed_null_rate: float | None = Field(default=None, ge=0, le=1)
    event_scope: list[str] = Field(default_factory=list)
    spec_only: bool = False
    description: str | None = None


class FunnelStep(ContractModel):
    order: int = Field(ge=1)
    event_name: str = Field(min_length=1)
    label: str | None = None


class FunnelDefinition(ContractModel):
    name: str = Field(min_length=1)
    entity_key: str = Field(min_length=1)
    steps: list[FunnelStep] = Field(min_length=2)
    ordered: bool
    description: str | None = None
    workflow_grain: str | None = None
    attribution_window: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(self.steps) + 1)):
            raise ValueError("funnel step order must be contiguous and start at 1")
        event_names = [step.event_name for step in self.steps]
        if len(event_names) != len(set(event_names)):
            raise ValueError("funnel steps must not repeat an event")
        return self


class ZeroDenominatorBehavior(StrEnum):
    ZERO = "zero"
    NULL = "null"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class MetricValueType(StrEnum):
    COUNT = "count"
    RATIO = "ratio"
    DURATION = "duration"
    CURRENCY = "currency"
    NUMBER = "number"


class MetricDefinition(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    numerator: str = Field(min_length=1)
    denominator: str = Field(min_length=1)
    entity_key: str = Field(min_length=1)
    aggregation_grain: str = Field(min_length=1)
    window: str = Field(min_length=1)
    zero_denominator_behavior: ZeroDenominatorBehavior
    value_type: MetricValueType
    currency_dimension: str | None = None
    fx_normalization_rule: str | None = None
    time_attribution: str | None = None
    deduplication_policy: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    computability: MetricComputability = MetricComputability.COMPUTABLE
    evidence_ids: list[str] = Field(default_factory=list)
    duration_start_event: str | None = None
    duration_end_event: str | None = None

    @model_validator(mode="after")
    def validate_currency_safety(self) -> Self:
        if (
            self.value_type == MetricValueType.CURRENCY
            and not self.currency_dimension
            and not self.fx_normalization_rule
        ):
            raise ValueError(
                "currency metrics require a currency dimension or FX-normalization rule"
            )
        return self


class NullHandling(StrEnum):
    KEEP = "keep"
    EXCLUDE = "exclude"
    MAP_TO_UNKNOWN = "map_to_unknown"


class NormalizationRule(ContractModel):
    source_values: list[str] = Field(min_length=1)
    normalized_value: str = Field(min_length=1)


class DimensionDefinition(ContractModel):
    name: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    description: str = Field(min_length=1)
    null_handling: NullHandling
    normalization_rules: list[NormalizationRule] = Field(default_factory=list)


class RelationshipCardinality(StrEnum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"


class RelationshipDefinition(ContractModel):
    name: str = Field(min_length=1)
    from_entity: str = Field(min_length=1)
    to_entity: str = Field(min_length=1)
    from_field: str = Field(min_length=1)
    to_field: str = Field(min_length=1)
    cardinality: RelationshipCardinality
    temporal_constraint: str | None = None
    description: str = Field(min_length=1)


class RuleSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class DataQualityRule(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: RuleSeverity
    expression: str = Field(min_length=1)
    affected_fields: list[str] = Field(default_factory=list)


class Observation(ContractModel):
    statement: str = Field(min_length=1)
    evidence_field_paths: list[str] = Field(default_factory=list)


class Assumption(ContractModel):
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    blocking: bool = False


class QuestionSupportClassification(StrEnum):
    COMPUTABLE_FROM_FEATURE = "computable_from_feature"
    REQUIRES_EXISTING_TABLES = "requires_existing_tables"
    REQUIRES_EXTERNAL_CONTEXT = "requires_external_context"
    NOT_COMPUTABLE = "not_computable"


class OpenQuestion(ContractModel):
    question: str = Field(min_length=1)
    classification: QuestionSupportClassification
    blocking: bool = False
    context: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class AnalyticsContract(ContractModel):
    contract_version: str
    feature: FeatureMetadata
    source: SourceMetadata
    grain: str = Field(min_length=1)
    primary_entity: str = Field(min_length=1)
    secondary_entities: list[str] = Field(default_factory=list)
    entities: list[EntityDefinition] = Field(min_length=1)
    events: list[EventDefinition] = Field(min_length=1)
    fields: list[FieldDefinition] = Field(min_length=1)
    funnels: list[FunnelDefinition] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    dimensions: list[DimensionDefinition] = Field(default_factory=list)
    relationships: list[RelationshipDefinition] = Field(default_factory=list)
    data_quality_rules: list[DataQualityRule] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    context_version_id: UUID | None = None
    context_content_sha256: Sha256 | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("contract_version")
    @classmethod
    def validate_contract_version(cls, value: str) -> str:
        if not _CONTRACT_VERSION.fullmatch(value):
            raise ValueError("unsupported contract_version; expected 1.0")
        return value

    @classmethod
    def model_validate_with_profile(
        cls,
        value: Any,
        source_profile: SourceProfile,
    ) -> Self:
        return cls.model_validate(value, context={"source_profile": source_profile})

    @model_validator(mode="after")
    def validate_semantics(self, info: ValidationInfo) -> Self:
        _require_unique("entity", [entity.name for entity in self.entities])
        _require_unique("entity field path", [entity.field_path for entity in self.entities])
        _require_unique("event", [event.name for event in self.events])
        _require_unique("field", [field.name for field in self.fields])
        _require_unique("source field path", [field.source_path for field in self.fields])
        _require_unique("funnel", [funnel.name for funnel in self.funnels])
        _require_unique("metric", [metric.name for metric in self.metrics])
        _require_unique("dimension", [dimension.name for dimension in self.dimensions])
        _require_unique("relationship", [relationship.name for relationship in self.relationships])
        _require_unique("data-quality rule", [rule.name for rule in self.data_quality_rules])

        entities = {entity.name: entity for entity in self.entities}
        entity_keys = {entity.field_path: entity for entity in self.entities}
        events = {event.name for event in self.events}
        fields = {field.source_path for field in self.fields}
        dimensions = {dimension.name for dimension in self.dimensions}
        dimension_fields = {dimension.field_path for dimension in self.dimensions}

        if self.primary_entity not in entities:
            raise ValueError("primary_entity must reference a declared entity")
        if entities[self.primary_entity].role != EntityRole.PRIMARY:
            raise ValueError("primary_entity must have the primary role")
        primary_entities = [
            entity.name for entity in self.entities if entity.role == EntityRole.PRIMARY
        ]
        if primary_entities != [self.primary_entity]:
            raise ValueError("exactly one entity must have the primary role")
        if len(self.secondary_entities) != len(set(self.secondary_entities)):
            raise ValueError("secondary_entities must be unique")
        for name in self.secondary_entities:
            if name not in entities:
                raise ValueError(f"secondary entity {name!r} is not declared")
            if entities[name].role != EntityRole.SECONDARY:
                raise ValueError(f"secondary entity {name!r} must have the secondary role")
        undeclared_entity_fields = sorted(set(entity_keys) - fields)
        if undeclared_entity_fields:
            raise ValueError(
                f"entity field paths are absent from contract fields: {undeclared_entity_fields}"
            )

        for event in self.events:
            unknown_keys = sorted(set(event.entity_keys) - set(entity_keys))
            if unknown_keys:
                raise ValueError(
                    f"event {event.name!r} references undeclared entity keys: {unknown_keys}"
                )
        for field in self.fields:
            unknown_events = sorted(set(field.event_scope) - events)
            if unknown_events:
                raise ValueError(
                    f"field {field.source_path!r} references undeclared events: {unknown_events}"
                )
        for funnel in self.funnels:
            unknown_events = sorted({step.event_name for step in funnel.steps} - events)
            if unknown_events:
                raise ValueError(
                    f"funnel {funnel.name!r} references undeclared events: {unknown_events}"
                )
            entity = entity_keys.get(funnel.entity_key)
            if entity is None or not entity.stable:
                raise ValueError(f"funnel {funnel.name!r} requires a declared stable entity key")
        for metric in self.metrics:
            entity = entity_keys.get(metric.entity_key)
            if entity is None or not entity.stable:
                raise ValueError(f"metric {metric.name!r} requires a declared stable entity key")
            if metric.currency_dimension and metric.currency_dimension not in dimensions:
                raise ValueError(
                    f"metric {metric.name!r} references an undeclared currency dimension"
                )
            unknown_metric_dimensions = sorted(set(metric.dimensions) - dimension_fields)
            if unknown_metric_dimensions:
                raise ValueError(
                    f"metric {metric.name!r} references undeclared dimension fields: "
                    f"{unknown_metric_dimensions}"
                )
        for dimension in self.dimensions:
            if dimension.field_path not in fields:
                raise ValueError(
                    f"dimension {dimension.name!r} references an undeclared field path"
                )
        for relationship in self.relationships:
            if relationship.from_entity not in entities or relationship.to_entity not in entities:
                raise ValueError(
                    f"relationship {relationship.name!r} references an undeclared entity"
                )
            if relationship.from_field not in fields or relationship.to_field not in fields:
                raise ValueError(
                    f"relationship {relationship.name!r} references an undeclared field path"
                )
        for rule in self.data_quality_rules:
            unknown_fields = sorted(set(rule.affected_fields) - fields)
            if unknown_fields:
                raise ValueError(
                    f"data-quality rule {rule.name!r} references undeclared fields: "
                    f"{unknown_fields}"
                )
        for observation in self.observations:
            unknown_fields = sorted(set(observation.evidence_field_paths) - fields)
            if unknown_fields:
                raise ValueError(
                    f"observation references undeclared evidence fields: {unknown_fields}"
                )

        profile = (info.context or {}).get("source_profile")
        if profile is not None:
            self._validate_source_profile(profile)
        return self

    def _validate_source_profile(self, profile: SourceProfile) -> None:
        if self.source.events_sha256 != profile.file.sha256:
            raise ValueError("contract source checksum does not match SourceProfile")
        if self.source.row_count != profile.file.valid_row_count:
            raise ValueError("contract source row count does not match SourceProfile")
        if (
            profile.time_coverage.minimum is not None
            and self.source.observed_window.start != profile.time_coverage.minimum
        ):
            raise ValueError("contract source window start does not match SourceProfile")
        if (
            profile.time_coverage.maximum is not None
            and self.source.observed_window.end != profile.time_coverage.maximum
        ):
            raise ValueError("contract source window end does not match SourceProfile")

        missing_paths = sorted(
            field.source_path
            for field in self.fields
            if not field.spec_only and field.source_path not in profile.field_paths
        )
        if missing_paths:
            raise ValueError(
                f"contract source paths are absent from SourceProfile: {missing_paths}"
            )

        incorrectly_spec_only_paths = sorted(
            field.source_path
            for field in self.fields
            if field.spec_only and field.source_path in profile.field_paths
        )
        if incorrectly_spec_only_paths:
            raise ValueError(
                f"observed source paths must not be marked spec_only: {incorrectly_spec_only_paths}"
            )

        declared_events = {event.name for event in self.events}
        missing_events = sorted(profile.event_names - declared_events)
        if missing_events:
            raise ValueError(f"observed events are absent from contract: {missing_events}")

        invented_observed_events = sorted(
            event.name
            for event in self.events
            if not event.spec_only and event.name not in profile.event_names
        )
        if invented_observed_events:
            raise ValueError(
                "contract events are absent from SourceProfile and not spec_only: "
                f"{invented_observed_events}"
            )

        incorrectly_spec_only_events = sorted(
            event.name
            for event in self.events
            if event.spec_only and event.name in profile.event_names
        )
        if incorrectly_spec_only_events:
            raise ValueError(
                f"observed events must not be marked spec_only: {incorrectly_spec_only_events}"
            )


def _require_unique(label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} names must be unique")
