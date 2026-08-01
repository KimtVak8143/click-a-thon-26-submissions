from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.models import AnalyticsContract
from app.profiling.profiler import SourceProfiler

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def source_profile():
    return SourceProfiler().profile(FIXTURES / "express_checkout_events.ndjson")


@pytest.fixture
def contract_data(source_profile) -> dict:
    return {
        "contract_version": "1.0",
        "feature": {
            "slug": "express_checkout",
            "name": "Express Checkout",
            "objective": "Improve checkout conversion",
        },
        "source": {
            "spec_sha256": "a" * 64,
            "events_sha256": source_profile.file.sha256,
            "row_count": source_profile.file.valid_row_count,
            "observed_window": {
                "start": source_profile.time_coverage.minimum,
                "end": source_profile.time_coverage.maximum,
            },
        },
        "grain": "one emitted feature event",
        "primary_entity": "application",
        "secondary_entities": [],
        "entities": [
            {
                "name": "application",
                "field_path": "application_id",
                "description": "Visa application journey",
                "role": "primary",
                "stable": True,
            }
        ],
        "events": [
            {
                "name": "express_checkout_shown",
                "description": "Express checkout was shown",
                "entity_keys": ["application_id"],
            },
            {
                "name": "express_payment_confirmed",
                "description": "Express payment completed",
                "entity_keys": ["application_id"],
            },
        ],
        "fields": [
            {
                "name": "application_id",
                "source_path": "application_id",
                "semantic_type": "identifier",
                "clickhouse_type": "String",
                "observed_null_rate": 0,
            },
            {
                "name": "payment_amount",
                "source_path": "payment.amount",
                "semantic_type": "currency",
                "clickhouse_type": "Decimal(18, 4)",
                "observed_null_rate": 0,
            },
            {
                "name": "payment_currency",
                "source_path": "payment.currency",
                "semantic_type": "string",
                "clickhouse_type": "LowCardinality(String)",
                "observed_null_rate": 0,
            },
            {
                "name": "device_type",
                "source_path": "device_type",
                "semantic_type": "string",
                "clickhouse_type": "LowCardinality(String)",
                "observed_null_rate": 0,
            },
        ],
        "funnels": [
            {
                "name": "express_checkout_funnel",
                "entity_key": "application_id",
                "ordered": True,
                "steps": [
                    {"order": 1, "event_name": "express_checkout_shown"},
                    {"order": 2, "event_name": "express_payment_confirmed"},
                ],
            }
        ],
        "metrics": [
            {
                "name": "checkout_conversion",
                "description": "Completed applications divided by shown applications",
                "numerator": "count(express_payment_confirmed)",
                "denominator": "count(express_checkout_shown)",
                "entity_key": "application_id",
                "aggregation_grain": "application",
                "window": "observed source window",
                "zero_denominator_behavior": "null",
                "value_type": "ratio",
            }
        ],
        "dimensions": [
            {
                "name": "payment_currency",
                "field_path": "payment.currency",
                "description": "Payment currency",
                "null_handling": "exclude",
                "normalization_rules": [],
            }
        ],
        "relationships": [],
        "data_quality_rules": [
            {
                "name": "application_id_required",
                "description": "Application identifier is required",
                "severity": "blocking",
                "expression": "application_id IS NOT NULL",
                "affected_fields": ["application_id"],
            }
        ],
        "observations": [
            {
                "statement": "Payment amount is present in both events.",
                "evidence_field_paths": ["payment.amount"],
            }
        ],
        "assumptions": [
            {
                "statement": "Application identifiers are stable across the funnel.",
                "rationale": "Both observed steps share the same identifier.",
            }
        ],
        "open_questions": [
            {
                "question": "Are retries emitted more than once?",
                "blocking": False,
            }
        ],
    }


def test_valid_contract_is_validated_against_profile(contract_data, source_profile) -> None:
    contract = AnalyticsContract.model_validate_with_profile(contract_data, source_profile)

    assert contract.contract_version == "1.0"
    assert contract.primary_entity == "application"
    assert contract.observations[0].statement != contract.assumptions[0].statement


def test_unknown_contract_fields_are_rejected(contract_data, source_profile) -> None:
    contract_data["feature"]["unexpected"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_contract_version_is_explicitly_validated(contract_data, source_profile) -> None:
    contract_data["contract_version"] = "2.0"

    with pytest.raises(ValidationError, match="unsupported contract_version"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_invalid_funnel_event_reference_is_rejected(contract_data, source_profile) -> None:
    contract_data["funnels"][0]["steps"][1]["event_name"] = "unobserved_completion"

    with pytest.raises(ValidationError, match="references undeclared events"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_missing_funnel_entity_key_is_rejected(contract_data, source_profile) -> None:
    del contract_data["funnels"][0]["entity_key"]

    with pytest.raises(ValidationError, match="entity_key"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_unstable_funnel_entity_key_is_rejected(contract_data, source_profile) -> None:
    contract_data["entities"][0]["stable"] = False

    with pytest.raises(ValidationError, match="requires a declared stable entity key"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_metric_requires_all_deterministic_operands(contract_data, source_profile) -> None:
    del contract_data["metrics"][0]["denominator"]

    with pytest.raises(ValidationError, match="denominator"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_unsafe_cross_currency_metric_is_rejected(contract_data, source_profile) -> None:
    metric = contract_data["metrics"][0]
    metric["value_type"] = "currency"

    with pytest.raises(ValidationError, match="currency dimension or FX-normalization"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_currency_metric_with_declared_dimension_is_valid(contract_data, source_profile) -> None:
    metric = contract_data["metrics"][0]
    metric["value_type"] = "currency"
    metric["currency_dimension"] = "payment_currency"

    contract = AnalyticsContract.model_validate_with_profile(contract_data, source_profile)

    assert contract.metrics[0].currency_dimension == "payment_currency"


def test_unobserved_source_path_requires_spec_only(contract_data, source_profile) -> None:
    new_field = {
        "name": "future_field",
        "source_path": "future.field",
        "semantic_type": "string",
        "clickhouse_type": "Nullable(String)",
    }
    contract_data["fields"].append(new_field)

    with pytest.raises(ValidationError, match="absent from SourceProfile"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)

    new_field["spec_only"] = True
    contract = AnalyticsContract.model_validate_with_profile(contract_data, source_profile)
    assert contract.fields[-1].spec_only is True


def test_every_observed_event_must_be_declared(contract_data, source_profile) -> None:
    contract_data["events"] = contract_data["events"][:1]
    contract_data["funnels"] = []

    with pytest.raises(ValidationError, match="observed events are absent from contract"):
        AnalyticsContract.model_validate_with_profile(contract_data, source_profile)


def test_contract_input_is_not_mutated(contract_data, source_profile) -> None:
    original = deepcopy(contract_data)
    AnalyticsContract.model_validate_with_profile(contract_data, source_profile)
    assert contract_data == original
