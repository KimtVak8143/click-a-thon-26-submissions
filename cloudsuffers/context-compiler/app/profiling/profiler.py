import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.profiling.models import (
    CandidateIdentifier,
    DataQualityCode,
    DataQualityObservation,
    EventCount,
    EventProfile,
    FieldProfile,
    FileMetadata,
    JsonType,
    ProfilerLimits,
    SourceProfile,
    TimeCoverage,
)

_JSON_TYPE_ORDER = {json_type: index for index, json_type in enumerate(JsonType)}
_NUMERIC_STRING = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_ISO2_FIELD_NAMES = {
    "country",
    "country_code",
    "destination",
    "destination_code",
    "destination_country_code",
    "geoip_country_code",
}
_EXPLICIT_IDENTIFIER_NAMES = {
    "id",
    "event_id",
    "user_id",
    "application_id",
    "group_id",
    "share_id",
}
_SENSITIVE_EXAMPLE_NAMES = {"payload", "raw_payload"}


@dataclass(frozen=True)
class ProfilerOptions:
    example_limit: int = 5
    distinct_limit: int = 10_000
    example_string_length: int = 128
    event_name_fields: tuple[str, ...] = ("event_name", "event")
    timestamp_fields: tuple[str, ...] = ("event_time", "timestamp")

    def __post_init__(self) -> None:
        if self.example_limit < 0:
            raise ValueError("example_limit must be non-negative")
        if self.distinct_limit < 1:
            raise ValueError("distinct_limit must be positive")
        if self.example_string_length < 1:
            raise ValueError("example_string_length must be positive")
        if not self.event_name_fields or not self.timestamp_fields:
            raise ValueError("event and timestamp field aliases cannot be empty")


@dataclass
class _FieldAccumulator:
    path: str
    present_rows: int = 0
    null_rows: int = 0
    observed_types: set[JsonType] = field(default_factory=set)
    event_names: set[str] = field(default_factory=set)
    examples: list[bool | int | float | str] = field(default_factory=list)
    example_keys: set[str] = field(default_factory=set)
    distinct_hashes: set[bytes] = field(default_factory=set)
    distinct_capped: bool = False
    numeric_minimum: int | float | None = None
    numeric_maximum: int | float | None = None
    string_length_minimum: int | None = None
    string_length_maximum: int | None = None
    array_element_types: set[JsonType] = field(default_factory=set)
    array_signatures: set[tuple[JsonType, ...]] = field(default_factory=set)
    numeric_string_count: int = 0
    empty_string_count: int = 0
    iso2_violation_count: int = 0
    non_null_value_count: int = 0

    def observe(
        self,
        values: list[Any],
        *,
        event_name: str | None,
        options: ProfilerOptions,
    ) -> None:
        self.present_rows += 1
        if event_name is not None:
            self.event_names.add(event_name)
        if any(value is None for value in values):
            self.null_rows += 1

        for value in values:
            value_type = _json_type(value)
            self.observed_types.add(value_type)
            if value is None:
                continue
            self.non_null_value_count += 1
            self._observe_distinct(value, options.distinct_limit)
            self._observe_example(value, options)
            if value_type in {JsonType.INTEGER, JsonType.NUMBER}:
                self._observe_numeric(value)
            elif value_type == JsonType.STRING:
                self._observe_string(value)
            elif value_type == JsonType.ARRAY:
                self._observe_array(value)

    def _observe_distinct(self, value: Any, limit: int) -> None:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).digest()
        if digest in self.distinct_hashes:
            return
        if len(self.distinct_hashes) < limit:
            self.distinct_hashes.add(digest)
        else:
            self.distinct_capped = True

    def _observe_example(self, value: Any, options: ProfilerOptions) -> None:
        if _suppress_examples(self.path) or len(self.examples) >= options.example_limit:
            return
        example: bool | int | float | str
        if isinstance(value, dict):
            example = "<object>"
        elif isinstance(value, list):
            example = f"<array:length={len(value)}>"
        elif isinstance(value, str):
            example = value[: options.example_string_length]
        else:
            example = value
        key = json.dumps(example, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if key not in self.example_keys:
            self.example_keys.add(key)
            self.examples.append(example)

    def _observe_numeric(self, value: int | float) -> None:
        self.numeric_minimum = (
            value if self.numeric_minimum is None else min(self.numeric_minimum, value)
        )
        self.numeric_maximum = (
            value if self.numeric_maximum is None else max(self.numeric_maximum, value)
        )

    def _observe_string(self, value: str) -> None:
        length = len(value)
        self.string_length_minimum = (
            length
            if self.string_length_minimum is None
            else min(self.string_length_minimum, length)
        )
        self.string_length_maximum = (
            length
            if self.string_length_maximum is None
            else max(self.string_length_maximum, length)
        )
        if value == "":
            self.empty_string_count += 1
        if _NUMERIC_STRING.fullmatch(value):
            self.numeric_string_count += 1
        if _is_iso2_candidate(self.path) and not re.fullmatch(r"[A-Z]{2}", value):
            self.iso2_violation_count += 1

    def _observe_array(self, value: list[Any]) -> None:
        signature = tuple(sorted({_json_type(item) for item in value}, key=_JSON_TYPE_ORDER.get))
        self.array_signatures.add(signature)
        self.array_element_types.update(signature)


class SourceProfiler:
    def __init__(self, options: ProfilerOptions | None = None) -> None:
        self.options = options or ProfilerOptions()

    def profile(self, path: Path) -> SourceProfile:
        checksum = hashlib.sha256()
        size_bytes = 0
        total_lines = 0
        valid_rows = 0
        malformed_rows = 0
        empty_lines = 0
        invalid_timestamps = 0
        minimum_timestamp: datetime | None = None
        maximum_timestamp: datetime | None = None
        unknown_event_names = 0
        event_counts: dict[str, int] = defaultdict(int)
        fields: dict[str, _FieldAccumulator] = {}

        with path.open("rb") as source:
            for raw_line in source:
                checksum.update(raw_line)
                size_bytes += len(raw_line)
                total_lines += 1
                if not raw_line.strip():
                    empty_lines += 1
                    continue

                try:
                    row = json.loads(raw_line, parse_constant=_reject_json_constant)
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    malformed_rows += 1
                    continue
                if not isinstance(row, dict):
                    malformed_rows += 1
                    continue
                if _contains_non_finite_number(row):
                    malformed_rows += 1
                    continue

                valid_rows += 1
                event_name = _first_nonempty_string(row, self.options.event_name_fields)
                if event_name is None:
                    unknown_event_names += 1
                else:
                    event_counts[event_name] += 1

                timestamp = _parse_timestamp(_first_present(row, self.options.timestamp_fields))
                if timestamp is None:
                    invalid_timestamps += 1
                else:
                    minimum_timestamp = (
                        timestamp
                        if minimum_timestamp is None
                        else min(minimum_timestamp, timestamp)
                    )
                    maximum_timestamp = (
                        timestamp
                        if maximum_timestamp is None
                        else max(maximum_timestamp, timestamp)
                    )

                row_fields: dict[str, list[Any]] = defaultdict(list)
                _collect_fields(row, row_fields)
                for field_path, values in row_fields.items():
                    accumulator = fields.setdefault(field_path, _FieldAccumulator(field_path))
                    accumulator.observe(values, event_name=event_name, options=self.options)

        field_profiles = [
            _build_field_profile(accumulator, valid_rows)
            for _, accumulator in sorted(fields.items())
        ]
        known_events = frozenset(event_counts)
        observations = _build_observations(
            fields,
            known_events=known_events,
            unknown_event_names=unknown_event_names,
            invalid_timestamps=invalid_timestamps,
        )
        candidates = [
            _build_identifier(accumulator, valid_rows)
            for path_key, accumulator in sorted(fields.items())
            if _is_identifier_path(path_key)
        ]

        return SourceProfile(
            file=FileMetadata(
                sha256=checksum.hexdigest(),
                size_bytes=size_bytes,
                total_line_count=total_lines,
                valid_row_count=valid_rows,
                malformed_row_count=malformed_rows,
                empty_line_count=empty_lines,
            ),
            time_coverage=TimeCoverage(
                minimum=minimum_timestamp,
                maximum=maximum_timestamp,
                invalid_timestamp_count=invalid_timestamps,
            ),
            event_profile=EventProfile(
                events=[
                    EventCount(
                        event_name=name,
                        count=count,
                        percentage_of_valid_rows=_ratio(count, valid_rows, percentage=True),
                    )
                    for name, count in sorted(event_counts.items())
                ],
                unknown_or_missing_event_name_count=unknown_event_names,
            ),
            fields=field_profiles,
            candidate_identifiers=candidates,
            data_quality_observations=observations,
            limits=ProfilerLimits(
                example_values=self.options.example_limit,
                distinct_values=self.options.distinct_limit,
                example_string_length=self.options.example_string_length,
            ),
        )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _json_type(value: Any) -> JsonType:
    if value is None:
        return JsonType.NULL
    if isinstance(value, bool):
        return JsonType.BOOLEAN
    if isinstance(value, int):
        return JsonType.INTEGER
    if isinstance(value, float):
        return JsonType.NUMBER
    if isinstance(value, str):
        return JsonType.STRING
    if isinstance(value, dict):
        return JsonType.OBJECT
    return JsonType.ARRAY


def _collect_fields(
    value: dict[str, Any],
    output: dict[str, list[Any]],
    prefix: str = "",
) -> None:
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        output[path].append(child)
        if isinstance(child, dict):
            _collect_fields(child, output, path)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, dict):
                    _collect_fields(item, output, f"{path}[]")


def _first_nonempty_string(row: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    value = _first_present(row, fields)
    return value if isinstance(value, str) and value.strip() else None


def _first_present(row: dict[str, Any], fields: tuple[str, ...]) -> Any:
    return next((row[field] for field in fields if field in row), None)


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                return None
            seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
            parsed = datetime.fromtimestamp(seconds, tz=UTC)
        elif isinstance(value, str) and value.strip():
            normalized = value.strip()
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        else:
            return None
    except (OverflowError, OSError, ValueError):
        return None
    return parsed.astimezone(UTC)


def _is_identifier_path(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
    return leaf in _EXPLICIT_IDENTIFIER_NAMES or leaf.endswith("_id")


def _suppress_examples(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
    return _is_identifier_path(path) or leaf in _SENSITIVE_EXAMPLE_NAMES


def _is_iso2_candidate(path: str) -> bool:
    return path.rsplit(".", 1)[-1].removesuffix("[]") in _ISO2_FIELD_NAMES


def _contains_non_finite_number(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite_number(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite_number(child) for child in value)
    return False


def _ratio(numerator: int, denominator: int, *, percentage: bool = False) -> float:
    if denominator == 0:
        return 0.0
    multiplier = 100 if percentage else 1
    return round(numerator / denominator * multiplier, 6)


def _build_field_profile(
    accumulator: _FieldAccumulator,
    valid_rows: int,
) -> FieldProfile:
    return FieldProfile(
        path=accumulator.path,
        observed_types=sorted(accumulator.observed_types, key=_JSON_TYPE_ORDER.get),
        presence_count=accumulator.present_rows,
        presence_rate=_ratio(accumulator.present_rows, valid_rows),
        null_count=accumulator.null_rows,
        null_rate=_ratio(accumulator.null_rows, accumulator.present_rows),
        examples=accumulator.examples,
        distinct_count=len(accumulator.distinct_hashes),
        distinct_count_mode="lower_bound" if accumulator.distinct_capped else "exact",
        numeric_minimum=accumulator.numeric_minimum,
        numeric_maximum=accumulator.numeric_maximum,
        string_length_minimum=accumulator.string_length_minimum,
        string_length_maximum=accumulator.string_length_maximum,
        observed_in_events=sorted(accumulator.event_names),
        array_element_types=sorted(accumulator.array_element_types, key=_JSON_TYPE_ORDER.get),
    )


def _build_identifier(
    accumulator: _FieldAccumulator,
    valid_rows: int,
) -> CandidateIdentifier:
    distinct_count = len(accumulator.distinct_hashes)
    return CandidateIdentifier(
        field_path=accumulator.path,
        presence_count=accumulator.present_rows,
        non_null_count=accumulator.non_null_value_count,
        coverage=_ratio(accumulator.present_rows - accumulator.null_rows, valid_rows),
        uniqueness_ratio=_ratio(distinct_count, accumulator.non_null_value_count),
        uniqueness_ratio_mode="lower_bound" if accumulator.distinct_capped else "exact",
    )


def _build_observations(
    fields: dict[str, _FieldAccumulator],
    *,
    known_events: frozenset[str],
    unknown_event_names: int,
    invalid_timestamps: int,
) -> list[DataQualityObservation]:
    observations: list[DataQualityObservation] = []
    if unknown_event_names:
        observations.append(
            DataQualityObservation(
                code=DataQualityCode.MISSING_EVENT_NAME,
                severity="warning",
                message="Rows are missing a non-empty event name.",
                count=unknown_event_names,
            )
        )
    if invalid_timestamps:
        observations.append(
            DataQualityObservation(
                code=DataQualityCode.INVALID_TIMESTAMP,
                severity="warning",
                message="Rows are missing a valid event timestamp.",
                count=invalid_timestamps,
            )
        )

    for path, accumulator in sorted(fields.items()):
        non_null_types = accumulator.observed_types - {JsonType.NULL}
        if len(non_null_types) > 1:
            observations.append(
                _field_observation(
                    DataQualityCode.MIXED_TYPES,
                    path,
                    accumulator.present_rows,
                    "Field has multiple non-null JSON types.",
                    accumulator.event_names,
                )
            )
        if (
            len(known_events) > 1
            and accumulator.event_names
            and accumulator.event_names < known_events
        ):
            observations.append(
                _field_observation(
                    DataQualityCode.EVENT_SCOPED_FIELD,
                    path,
                    accumulator.present_rows,
                    "Field is present only for a subset of observed events.",
                    accumulator.event_names,
                    severity="info",
                )
            )
        if accumulator.iso2_violation_count:
            observations.append(
                _field_observation(
                    DataQualityCode.ISO2_VIOLATION,
                    path,
                    accumulator.iso2_violation_count,
                    "ISO-2 candidate contains values outside two uppercase ASCII letters.",
                    accumulator.event_names,
                )
            )
        if accumulator.numeric_string_count:
            observations.append(
                _field_observation(
                    DataQualityCode.NUMERIC_STRING,
                    path,
                    accumulator.numeric_string_count,
                    "Field contains numeric values encoded as strings.",
                    accumulator.event_names,
                    severity="info",
                )
            )
        if accumulator.empty_string_count and accumulator.null_rows:
            observations.append(
                _field_observation(
                    DataQualityCode.EMPTY_STRING_AND_NULL,
                    path,
                    accumulator.empty_string_count + accumulator.null_rows,
                    "Field uses both empty strings and null values.",
                    accumulator.event_names,
                )
            )
        non_null_array_types = accumulator.array_element_types - {JsonType.NULL}
        if len(non_null_array_types) > 1 or len(accumulator.array_signatures) > 1:
            observations.append(
                _field_observation(
                    DataQualityCode.INCONSISTENT_ARRAY,
                    path,
                    accumulator.present_rows,
                    "Array element types are inconsistent across or within rows.",
                    accumulator.event_names,
                )
            )

    return sorted(
        observations,
        key=lambda item: (item.code.value, item.field_path or "", item.event_names),
    )


def _field_observation(
    code: DataQualityCode,
    path: str,
    count: int,
    message: str,
    event_names: set[str],
    *,
    severity: str = "warning",
) -> DataQualityObservation:
    return DataQualityObservation(
        code=code,
        severity=severity,
        message=message,
        count=count,
        field_path=path,
        event_names=sorted(event_names),
    )
