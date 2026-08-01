from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JsonType(StrEnum):
    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    OBJECT = "object"
    ARRAY = "array"


class FileMetadata(StrictModel):
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    total_line_count: int = Field(ge=0)
    valid_row_count: int = Field(ge=0)
    malformed_row_count: int = Field(ge=0)
    empty_line_count: int = Field(ge=0)


class TimeCoverage(StrictModel):
    minimum: datetime | None = None
    maximum: datetime | None = None
    invalid_timestamp_count: int = Field(ge=0)


class EventCount(StrictModel):
    event_name: str = Field(min_length=1)
    count: int = Field(ge=1)
    percentage_of_valid_rows: float = Field(ge=0, le=100)


class EventProfile(StrictModel):
    events: list[EventCount]
    unknown_or_missing_event_name_count: int = Field(ge=0)


class FieldProfile(StrictModel):
    path: str = Field(min_length=1)
    observed_types: list[JsonType]
    presence_count: int = Field(ge=1)
    presence_rate: float = Field(ge=0, le=1)
    null_count: int = Field(ge=0)
    null_rate: float = Field(ge=0, le=1)
    examples: list[bool | int | float | str]
    distinct_count: int = Field(ge=0)
    distinct_count_mode: Literal["exact", "lower_bound"]
    numeric_minimum: int | float | None = None
    numeric_maximum: int | float | None = None
    string_length_minimum: int | None = Field(default=None, ge=0)
    string_length_maximum: int | None = Field(default=None, ge=0)
    observed_in_events: list[str]
    array_element_types: list[JsonType]


class CandidateIdentifier(StrictModel):
    field_path: str = Field(min_length=1)
    presence_count: int = Field(ge=1)
    non_null_count: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    uniqueness_ratio: float = Field(ge=0, le=1)
    uniqueness_ratio_mode: Literal["exact", "lower_bound"]
    empty_string_count: int = Field(default=0, ge=0)
    non_empty_coverage: float = Field(default=0, ge=0, le=1)


class NamedKeyCoverage(StrictModel):
    field_path: Literal["application_id", "session_id", "user_id"]
    presence_rate: float = Field(ge=0, le=1)
    non_null_rate: float = Field(ge=0, le=1)
    non_empty_rate: float = Field(ge=0, le=1)


class DuplicateEventIdProfile(StrictModel):
    field_path: str
    non_null_count: int = Field(ge=0)
    distinct_count: int = Field(ge=0)
    duplicate_count_lower_bound: int = Field(ge=0)
    duplicate_rate_lower_bound: float = Field(ge=0, le=1)
    distinct_count_mode: Literal["exact", "lower_bound"]


class CurrencyFieldProfile(StrictModel):
    field_path: str
    presence_rate: float = Field(ge=0, le=1)
    distinct_count: int = Field(ge=0)
    distinct_count_mode: Literal["exact", "lower_bound"]


class CanonicalDimensionCandidate(StrictModel):
    field_path: str
    canonical_dimension: Literal["device", "os", "geo", "destination", "app_version"]
    presence_rate: float = Field(ge=0, le=1)


class TimeQualityIndicators(StrictModel):
    timestamp_field_candidates: list[str] = Field(default_factory=list)
    parsed_timestamp_count: int = Field(ge=0)
    non_monotonic_transition_count: int = Field(ge=0)
    source_order_monotonic: bool


class DataQualityCode(StrEnum):
    MIXED_TYPES = "mixed_types"
    MISSING_EVENT_NAME = "missing_event_name"
    INVALID_TIMESTAMP = "invalid_timestamp"
    EVENT_SCOPED_FIELD = "event_scoped_field"
    ISO2_VIOLATION = "iso2_candidate_violation"
    NUMERIC_STRING = "numeric_string"
    EMPTY_STRING_AND_NULL = "empty_string_and_null"
    INCONSISTENT_ARRAY = "inconsistent_array"


class DataQualityObservation(StrictModel):
    code: DataQualityCode
    severity: Literal["info", "warning"]
    message: str = Field(min_length=1)
    count: int = Field(ge=1)
    field_path: str | None = None
    event_names: list[str] = Field(default_factory=list)


class ProfilerLimits(StrictModel):
    example_values: int = Field(ge=0)
    distinct_values: int = Field(ge=1)
    example_string_length: int = Field(ge=1)


class SourceProfile(StrictModel):
    profile_version: Literal["1.0"] = "1.0"
    file: FileMetadata
    time_coverage: TimeCoverage
    event_profile: EventProfile
    fields: list[FieldProfile]
    candidate_identifiers: list[CandidateIdentifier]
    data_quality_observations: list[DataQualityObservation]
    candidate_event_name_fields: list[str] = Field(default_factory=list)
    candidate_timestamp_fields: list[str] = Field(default_factory=list)
    named_key_coverage: list[NamedKeyCoverage] = Field(default_factory=list)
    duplicate_event_id: DuplicateEventIdProfile | None = None
    currency_fields: list[CurrencyFieldProfile] = Field(default_factory=list)
    canonical_dimension_candidates: list[CanonicalDimensionCandidate] = Field(default_factory=list)
    time_quality: TimeQualityIndicators | None = None
    limits: ProfilerLimits

    @property
    def field_paths(self) -> frozenset[str]:
        return frozenset(field.path for field in self.fields)

    @property
    def event_names(self) -> frozenset[str]:
        return frozenset(event.event_name for event in self.event_profile.events)

    def stable_json(self, *, indent: int | None = None) -> str:
        return self.model_dump_json(indent=indent, exclude_none=False)
