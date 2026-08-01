import json
import logging
from pathlib import Path

import pytest

from app.agents.instrumentation import feature_slug_from_spec
from app.contracts.intent import ContractIntent
from app.contracts.prompts import (
    build_generation_request,
    compact_json_schema,
    contract_intent_required_field_checklist,
)
from app.core.config import Settings
from app.llm.provider import OpenAICompatibleProvider
from app.llm.schema import (
    SchemaInliningError,
    count_schema_references,
    inline_local_json_schema_references,
)
from app.profiling.profiler import SourceProfiler
from tests.test_instrumentation_agent import SPEC

FIXTURE = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"


@pytest.fixture
def provider_request():
    profile = SourceProfiler().profile(FIXTURE)
    return build_generation_request(
        SPEC,
        profile,
        expected_feature_slug=feature_slug_from_spec(SPEC),
        context_summary=None,
    )


def test_provider_schema_inlines_nested_contract_intent_requirements(
    provider_request,
) -> None:
    provider = OpenAICompatibleProvider(
        Settings(
            llm_base_url="http://provider.invalid/v1",
            llm_api_key="test",
            llm_model="model",
            llm_structured_output_mode="json_schema",
            _env_file=None,
        )
    )

    payload = provider._payload(provider_request)
    schema = payload["response_format"]["json_schema"]["schema"]

    assert count_schema_references(provider_request.json_schema) > 0
    assert count_schema_references(schema) == 0
    assert "$defs" not in schema
    assert schema["required"] == [
        "feature",
        "entities",
        "primary_entity_id",
        "funnels",
        "metrics",
        "dimensions",
        "relationships",
        "observations",
        "assumptions",
        "open_questions",
    ]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["feature"]["required"] == [
        "slug",
        "name",
        "objective",
    ]
    assert schema["properties"]["feature"]["properties"]["slug"]["const"] == ("express_checkout")
    entity_schema = schema["properties"]["entities"]["items"]
    assert entity_schema["required"] == [
        "id",
        "key_field",
        "role",
        "description",
        "evidence_ids",
    ]
    assert entity_schema["additionalProperties"] is False
    assert schema["properties"]["entities"]["minItems"] == 1
    assert schema["properties"]["metrics"]["minItems"] == 1
    assert schema["properties"]["dimensions"]["minItems"] == 1
    assert schema["properties"]["entities"]["items"]["properties"]["key_field"]["enum"]
    assert schema["properties"]["funnels"]["items"]["properties"]["ordered_events"]["minItems"] == 2


def test_reference_inlining_preserves_required_arrays_and_array_limits() -> None:
    schema = {
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            }
        },
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"$ref": "#/$defs/Item"},
                "minItems": 1,
                "maxItems": 3,
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }

    inlined, diagnostics = inline_local_json_schema_references(schema)

    assert inlined["required"] == ["items"]
    assert inlined["properties"]["items"]["minItems"] == 1
    assert inlined["properties"]["items"]["maxItems"] == 3
    assert inlined["properties"]["items"]["items"]["required"] == ["name"]
    assert diagnostics.references_before_inlining == 1
    assert diagnostics.references_after_inlining == 0
    assert diagnostics.nested_required_array_count == 1


@pytest.mark.parametrize(
    "reference",
    ["https://example.invalid/schema.json", "#/$defs/Missing"],
)
def test_unknown_and_external_schema_references_are_rejected(reference) -> None:
    schema = {"$defs": {}, "$ref": reference}

    with pytest.raises(SchemaInliningError):
        inline_local_json_schema_references(schema)


def test_cyclic_schema_references_are_rejected() -> None:
    schema = {
        "$defs": {
            "A": {"$ref": "#/$defs/B"},
            "B": {"$ref": "#/$defs/A"},
        },
        "$ref": "#/$defs/A",
    }

    with pytest.raises(SchemaInliningError, match="cyclic"):
        inline_local_json_schema_references(schema)


def test_schema_diagnostics_are_safe(provider_request, caplog) -> None:
    marker = "SCHEMA_CONTENT_MUST_NOT_BE_LOGGED"
    provider_request.json_schema["properties"][marker] = {"type": "string"}
    provider = OpenAICompatibleProvider(
        Settings(
            llm_base_url="http://provider.invalid/v1",
            llm_api_key="test",
            llm_model="model",
            llm_structured_output_mode="json_schema",
            _env_file=None,
        )
    )

    with caplog.at_level(logging.INFO):
        payload = provider._payload(provider_request)

    schema = payload["response_format"]["json_schema"]["schema"]
    assert marker in schema["properties"]
    assert marker not in caplog.text
    record = next(
        item for item in caplog.records if item.getMessage() == "provider_schema_normalized"
    )
    assert record.schema_references_before > 0
    assert record.schema_references_after == 0
    assert record.nested_required_array_count > 0
    assert record.provider_schema_bytes > 0


def test_required_field_checklist_matches_contract_intent_model() -> None:
    schema = compact_json_schema(ContractIntent.model_json_schema())
    checklist = contract_intent_required_field_checklist()
    feature_required = schema["$defs"]["IntentFeature"]["required"]
    entity_required = schema["$defs"]["IntentEntity"]["required"]

    for name in feature_required:
        assert f"- {name}" in checklist
    for name in entity_required:
        assert f"- {name}" in checklist
    for name in schema["required"]:
        assert f"- {name}" in checklist
    assert "Feature requires:" in checklist
    assert "Every entity requires:" in checklist
    assert "The root requires:" in checklist
    assert json.dumps(schema) not in checklist
