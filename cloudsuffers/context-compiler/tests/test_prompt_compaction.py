import json
from pathlib import Path

from app.agents.instrumentation import feature_slug_from_spec
from app.context.bootstrap import build_base_context_bundle
from app.contracts.intent import ContractIntent
from app.contracts.models import AnalyticsContract
from app.contracts.prompts import (
    _filter_context_for_profile,
    build_generation_request,
    compact_json_schema,
    compact_source_profile,
)
from app.profiling.profiler import SourceProfiler

FIXTURES = Path(__file__).parent / "fixtures"
_PRESENTATION_KEYS = {
    "title",
    "description",
    "default",
    "example",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
}


def test_compact_profile_contains_only_generation_evidence(tmp_path: Path) -> None:
    raw_value = "RAW_IDENTIFIER_OR_PROMPT_INJECTION"
    events = tmp_path / "events.ndjson"
    events.write_text(
        json.dumps(
            {
                "event_name": "started",
                "event_time": "2026-01-01T00:00:00Z",
                "application_id": raw_value,
                "note": raw_value,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    profile = SourceProfiler().profile(events)

    compact = compact_source_profile(profile)
    serialized = json.dumps(compact, sort_keys=True)

    assert set(compact) == {
        "source",
        "events",
        "fields",
        "candidate_identifiers",
        "data_quality_observations",
        "semantic_profile_hints",
    }
    assert compact["source"] == {
        "events_sha256": profile.file.sha256,
        "row_count": 1,
        "observed_window": {
            "start": profile.time_coverage.minimum.isoformat(),
            "end": profile.time_coverage.maximum.isoformat(),
        },
    }
    assert compact["events"] == [{"name": "started", "count": 1}]
    application = next(item for item in compact["fields"] if item["path"] == "application_id")
    assert "identifier" in application["semantic_type_hints"]
    assert raw_value not in serialized
    assert "examples" not in serialized
    assert "distinct_count" not in serialized
    assert "size_bytes" not in serialized
    assert "total_line_count" not in serialized


def test_compact_schema_removes_only_presentation_metadata() -> None:
    original = ContractIntent.model_json_schema()
    compact = compact_json_schema(original)

    _assert_schema_preserved(original, compact)
    assert len(json.dumps(compact)) < len(json.dumps(original))
    assert set(compact["$defs"]["IntentMetric"]["required"]) >= {
        "id",
        "name",
        "description",
        "numerator",
        "denominator",
        "entity_id",
        "aggregation_grain",
        "analysis_window",
        "zero_denominator_behavior",
        "value_type",
    }
    assert compact["$defs"]["ZeroDenominatorBehavior"]["enum"] == [
        "zero",
        "null",
        "not_applicable",
        "error",
    ]
    _assert_all_references_resolve(compact)


def test_compact_prompt_measurements_and_no_raw_profile_values(tmp_path: Path) -> None:
    marker = "RAW_ROW_MARKER_NEVER_SEND"
    events = tmp_path / "events.ndjson"
    events.write_text(
        json.dumps(
            {
                "event_name": "started",
                "event_time": "2026-01-01T00:00:00Z",
                "application_id": "application-private-1",
                "note": marker,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    profile = SourceProfiler().profile(events)
    spec = "# Started\nTrack started by application_id."

    request = build_generation_request(
        spec,
        profile,
        expected_feature_slug=feature_slug_from_spec(spec),
        context_summary=None,
    )
    prompt = "".join(message.content for message in request.messages)

    assert marker not in prompt
    assert "application-private-1" not in prompt
    assert '"examples"' not in prompt
    assert request.measurements.prompt_bytes == len(prompt.encode())
    assert request.measurements.profile_summary_bytes > 0
    assert request.measurements.json_schema_bytes > 0
    assert "contract_intent_json_schema" in prompt
    assert "analytics_contract_json_schema" not in prompt
    assert "Return the ContractIntent fields directly at the JSON root" in prompt
    assert "Do not wrap the result under ContractIntent" in prompt
    assert "contract_version" not in json.dumps(request.json_schema)
    assert len(json.dumps(request.json_schema)) < len(
        json.dumps(compact_json_schema(AnalyticsContract.model_json_schema()))
    )


def test_prompt_uses_compact_context_projection_without_official_source_text() -> None:
    profile = SourceProfiler().profile(FIXTURES / "express_checkout_events.ndjson")
    source_path = Path(__file__).parents[1] / "docs" / "base_context.md"
    bundle = build_base_context_bundle(source_path)
    context = json.dumps(bundle.projection, sort_keys=True, separators=(",", ":"))

    request = build_generation_request(
        "# Express Checkout\nTrack express_checkout_shown then express_payment_confirmed.",
        profile,
        expected_feature_slug="express_checkout",
        context_summary=context,
        context_evidence_ids=["CTX-005", "base_context:hypothesis:K1"],
    )
    prompt = "\n".join(item.content for item in request.messages)

    assert "approved_context_projection_untrusted" in prompt
    assert "CTX-005" in prompt
    assert "base_context:hypothesis:K1" in prompt
    assert "session_purchase_conversion_rate" not in prompt
    assert "application_purchase_conversion_rate" not in prompt
    assert "700K+ applications annually" not in prompt
    assert "source_content" not in prompt


def _make_profile(tmp_path: Path, *, field: str = "application_id") -> object:
    events = tmp_path / "events.ndjson"
    events.write_text(
        json.dumps(
            {
                "event_name": "started",
                "event_time": "2026-01-01T00:00:00Z",
                field: "some-value",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return SourceProfiler().profile(events)


def _context_json(**kwargs) -> str:
    base = {"entities": [], "metrics": [], "issues": [], "grain_policy": "per_application"}
    return json.dumps({**base, **kwargs}, sort_keys=True)


def test_filter_context_keeps_grounded_entities_and_metrics(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path, field="application_id")
    ctx = _context_json(
        entities=[{"entity_id": "application", "key_fields": ["application_id"]}],
        metrics=[
            {
                "metric_id": "starts",
                "entity_key": "application_id",
                "numerator": "count(started)",
                "denominator": "count(started)",
            }
        ],
    )
    result = json.loads(_filter_context_for_profile(ctx, profile))
    assert len(result["entities"]) == 1
    assert result["entities"][0]["entity_id"] == "application"
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["metric_id"] == "starts"
    assert "ungrounded_context_omission_counts" not in result


def test_filter_context_removes_ungrounded_entities_and_metrics(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path, field="application_id")
    ctx = _context_json(
        entities=[
            {"entity_id": "application", "key_fields": ["application_id"]},
            {"entity_id": "session", "key_fields": ["session_id"]},
        ],
        metrics=[
            {
                "metric_id": "starts",
                "entity_key": "application_id",
                "numerator": "count(started)",
            },
            {
                "metric_id": "session_conversion_rate",
                "entity_key": "session_id",
                "numerator": "count(purchase_completed)",
            },
        ],
    )
    result = json.loads(_filter_context_for_profile(ctx, profile))
    assert [e["entity_id"] for e in result["entities"]] == ["application"]
    assert [m["metric_id"] for m in result["metrics"]] == ["starts"]
    assert result["ungrounded_context_omission_counts"] == {
        "entities": 1,
        "events": 0,
        "metrics": 1,
        "relationships": 0,
    }


def test_filter_context_preserves_passthrough_keys(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path, field="application_id")
    ctx = _context_json(
        issues=["some data quality issue"],
        canonical_funnel=["step_a", "step_b"],
        source_precedence=["clickstream"],
    )
    result = json.loads(_filter_context_for_profile(ctx, profile))
    assert result["issues"] == ["some data quality issue"]
    assert result["canonical_funnel"] == []
    assert result["ungrounded_context_omission_counts"]["events"] == 2
    assert result["source_precedence"] == ["clickstream"]
    assert result["grain_policy"] == "per_application"


def test_filter_context_handles_invalid_json_and_none_gracefully(tmp_path: Path) -> None:
    profile = _make_profile(tmp_path)
    assert _filter_context_for_profile(None, profile) is None
    malformed = "{not valid json"
    assert _filter_context_for_profile(malformed, profile) == malformed


def _assert_schema_preserved(
    original: object,
    compact: object,
    *,
    preserve_mapping_keys: bool = False,
) -> None:
    if isinstance(original, list):
        assert isinstance(compact, list)
        assert len(original) == len(compact)
        for source_item, compact_item in zip(original, compact, strict=True):
            _assert_schema_preserved(source_item, compact_item)
        return
    if not isinstance(original, dict):
        assert compact == original
        return
    assert isinstance(compact, dict)
    for key, value in original.items():
        if not preserve_mapping_keys and key in _PRESENTATION_KEYS:
            assert key not in compact
            continue
        assert key in compact
        _assert_schema_preserved(
            value,
            compact[key],
            preserve_mapping_keys=key in {"properties", "$defs", "definitions"},
        )


def _assert_all_references_resolve(schema: dict) -> None:
    references = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("$ref"), str):
                references.append(value["$ref"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    for reference in references:
        assert reference.startswith("#/$defs/")
        assert reference.removeprefix("#/$defs/") in schema["$defs"]
