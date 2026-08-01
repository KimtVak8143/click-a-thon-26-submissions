import hashlib
import json
import tracemalloc
from pathlib import Path

import pytest

from app.profiling.models import DataQualityCode, JsonType
from app.profiling.profiler import ProfilerOptions, SourceProfiler

FIXTURES = Path(__file__).parent / "fixtures"


def field_map(profile: object) -> dict[str, object]:
    return {field.path: field for field in profile.fields}


@pytest.mark.parametrize(
    ("fixture_name", "identifier"),
    [
        ("express_checkout_events.ndjson", "application_id"),
        ("group_family_events.ndjson", "group_id"),
        ("status_sharing_events.ndjson", "share_id"),
        ("recovery_events.ndjson", "application_id"),
        ("instant_forex_events.ndjson", "application_id"),
    ],
)
def test_all_documented_atlys_feature_event_shapes(
    fixture_name: str,
    identifier: str,
) -> None:
    profile = SourceProfiler().profile(FIXTURES / fixture_name)

    assert profile.file.valid_row_count == 2
    assert profile.file.malformed_row_count == 0
    assert identifier in {candidate.field_path for candidate in profile.candidate_identifiers}
    assert profile.time_coverage.minimum is not None
    assert profile.time_coverage.minimum.tzinfo is not None


def test_profiles_nested_payment_status_share_and_null_android_os() -> None:
    express = SourceProfiler().profile(FIXTURES / "express_checkout_events.ndjson")
    express_fields = field_map(express)

    assert express_fields["payment"].observed_types == [JsonType.OBJECT]
    assert express_fields["payment.amount"].numeric_minimum == 120.5
    assert express_fields["payment.currency"].examples == ["USD"]
    assert express_fields["os"].null_count == 1
    assert express_fields["os"].null_rate == 0.5

    sharing = SourceProfiler().profile(FIXTURES / "status_sharing_events.ndjson")
    share_identifier = next(
        item for item in sharing.candidate_identifiers if item.field_path == "share_id"
    )
    assert share_identifier.coverage == 1.0
    assert share_identifier.uniqueness_ratio == 0.5
    assert field_map(sharing)["share_id"].examples == []


def test_quality_observations_and_line_counts(tmp_path: Path) -> None:
    events = tmp_path / "quality.ndjson"
    events.write_bytes(
        b'{"event_name":"first","event_time":"2026-01-01T00:00:00Z",'
        b'"value":1,"numeric":"12.5","country":"USA","optional":"",'
        b'"items":[1,2],"payment":{"amount":10},"first_only":true}\n'
        b"\n"
        b"not-json\n"
        b'{"event_name":"second","event_time":"bad","value":"one",'
        b'"numeric":"7","country":"us","optional":null,'
        b'"items":["x",null],"payment":{"amount":"10"}}\n'
        b'{"event_time":"2026-01-02T00:00:00+00:00","value":2}\n'
    )

    profile = SourceProfiler().profile(events)
    fields = field_map(profile)
    codes = {(item.code, item.field_path) for item in profile.data_quality_observations}

    assert profile.file.total_line_count == 5
    assert profile.file.valid_row_count == 3
    assert profile.file.malformed_row_count == 1
    assert profile.file.empty_line_count == 1
    assert profile.time_coverage.invalid_timestamp_count == 1
    assert profile.event_profile.unknown_or_missing_event_name_count == 1
    assert [item.event_name for item in profile.event_profile.events] == ["first", "second"]
    assert fields["value"].observed_types == [JsonType.INTEGER, JsonType.STRING]
    assert fields["payment.amount"].observed_types == [JsonType.INTEGER, JsonType.STRING]
    assert fields["items"].array_element_types == [
        JsonType.NULL,
        JsonType.INTEGER,
        JsonType.STRING,
    ]
    assert (DataQualityCode.MIXED_TYPES, "value") in codes
    assert (DataQualityCode.NUMERIC_STRING, "numeric") in codes
    assert (DataQualityCode.INVALID_TIMESTAMP, None) in codes
    assert (DataQualityCode.MISSING_EVENT_NAME, None) in codes
    assert (DataQualityCode.ISO2_VIOLATION, "country") in codes
    assert (DataQualityCode.EMPTY_STRING_AND_NULL, "optional") in codes
    assert (DataQualityCode.INCONSISTENT_ARRAY, "items") in codes
    assert (DataQualityCode.EVENT_SCOPED_FIELD, "first_only") in codes


def test_nested_object_arrays_are_discovered(tmp_path: Path) -> None:
    events = tmp_path / "arrays.ndjson"
    events.write_text(
        '{"event":"cart","timestamp":1704067200000,'
        '"items":[{"sku":"a","price":1},{"sku":"b","price":2}]}\n',
        encoding="utf-8",
    )

    profile = SourceProfiler().profile(events)
    fields = field_map(profile)

    assert fields["items"].observed_types == [JsonType.ARRAY]
    assert fields["items"].array_element_types == [JsonType.OBJECT]
    assert fields["items[].sku"].presence_count == 1
    assert fields["items[].sku"].distinct_count == 2
    assert profile.time_coverage.minimum.isoformat() == "2024-01-01T00:00:00+00:00"


def test_limits_are_bounded_and_distinct_count_becomes_lower_bound(tmp_path: Path) -> None:
    events = tmp_path / "limits.ndjson"
    events.write_text(
        "".join(
            json.dumps(
                {
                    "event_name": "event",
                    "event_time": "2026-01-01T00:00:00Z",
                    "category": f"category-{index}",
                }
            )
            + "\n"
            for index in range(5)
        ),
        encoding="utf-8",
    )

    profile = SourceProfiler(
        ProfilerOptions(example_limit=2, distinct_limit=3, example_string_length=8)
    ).profile(events)
    category = field_map(profile)["category"]

    assert category.examples == ["category"]
    assert len(category.examples) <= 2
    assert category.distinct_count == 3
    assert category.distinct_count_mode == "lower_bound"


def test_output_and_hashes_are_deterministic(tmp_path: Path) -> None:
    events = FIXTURES / "express_checkout_events.ndjson"
    first = SourceProfiler().profile(events)
    second = SourceProfiler().profile(events)

    assert first.stable_json() == second.stable_json()
    assert first.file.sha256 == hashlib.sha256(events.read_bytes()).hexdigest()
    assert (
        hashlib.sha256(first.stable_json().encode()).hexdigest()
        == hashlib.sha256(second.stable_json().encode()).hexdigest()
    )


def test_streaming_memory_is_bounded_for_large_fixture(tmp_path: Path) -> None:
    events = tmp_path / "large.ndjson"
    payload = "x" * 2048
    with events.open("w", encoding="utf-8") as stream:
        for index in range(10_000):
            stream.write(
                json.dumps(
                    {
                        "event_id": f"event-{index}",
                        "event_name": "large_event",
                        "event_time": "2026-01-01T00:00:00Z",
                        "payload": payload,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

    tracemalloc.start()
    profile = SourceProfiler(ProfilerOptions(distinct_limit=10)).profile(events)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert events.stat().st_size > 20_000_000
    assert profile.file.valid_row_count == 10_000
    assert field_map(profile)["payload"].examples == []
    assert peak < 8_000_000


def test_non_finite_numbers_are_malformed_in_stable_json(tmp_path: Path) -> None:
    events = tmp_path / "non_finite.ndjson"
    events.write_text(
        '{"event_name":"bad","event_time":"2026-01-01T00:00:00Z","value":1e400}\n',
        encoding="utf-8",
    )

    profile = SourceProfiler().profile(events)

    assert profile.file.valid_row_count == 0
    assert profile.file.malformed_row_count == 1
    assert "Infinity" not in profile.stable_json()


def test_semantic_profile_hints_are_value_redacted_and_deterministic(tmp_path: Path) -> None:
    events = tmp_path / "semantic_hints.ndjson"
    rows = [
        {
            "event_id": "secret-event-1",
            "event_name": "started",
            "event_time": "2026-01-01T00:01:00Z",
            "application_id": "application-secret",
            "session_id": "session-secret",
            "user_id": "user-secret",
            "device_type": "mobile",
            "os": "test-os",
            "geoip_country_code": "IN",
            "destination": "US",
            "app_version": "1.0",
            "currency": "USD",
        },
        {
            "event_id": "secret-event-1",
            "event_name": "completed",
            "event_time": "2026-01-01T00:00:00Z",
            "application_id": "",
            "session_id": "session-secret",
            "user_id": "user-secret",
            "device_type": "mobile",
            "os": "test-os",
            "geoip_country_code": "IN",
            "destination": "US",
            "app_version": "1.0",
            "currency": "INR",
        },
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    profile = SourceProfiler().profile(events)
    serialized = profile.stable_json()

    assert profile.candidate_event_name_fields == ["event_name"]
    assert profile.candidate_timestamp_fields == ["event_time"]
    application = next(
        item for item in profile.named_key_coverage if item.field_path == "application_id"
    )
    assert application.presence_rate == 1
    assert application.non_empty_rate == 0.5
    assert profile.duplicate_event_id.duplicate_count_lower_bound == 1
    assert profile.duplicate_event_id.duplicate_rate_lower_bound == 0.5
    assert {item.field_path for item in profile.currency_fields} == {"currency"}
    assert profile.currency_fields[0].distinct_count == 2
    assert {
        (item.field_path, item.canonical_dimension)
        for item in profile.canonical_dimension_candidates
    } >= {
        ("device_type", "device"),
        ("os", "os"),
        ("geoip_country_code", "geo"),
        ("destination", "destination"),
        ("app_version", "app_version"),
    }
    assert profile.time_quality.non_monotonic_transition_count == 1
    assert profile.time_quality.source_order_monotonic is False
    assert "secret-event-1" not in serialized
    assert "application-secret" not in serialized
    assert "session-secret" not in serialized
    assert "user-secret" not in serialized
