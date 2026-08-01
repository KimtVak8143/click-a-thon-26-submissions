import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.agents.instrumentation import ContractGenerationResult, InstrumentationAgent
from app.context.bootstrap import build_base_context_bundle
from app.contracts.models import MetricValueType
from app.core.config import Settings
from app.llm.provider import OpenAICompatibleProvider
from app.profiling.models import JsonType
from app.profiling.profiler import SourceProfiler

FIXTURES = Path(__file__).parent / "fixtures" / "generalization"
BASE_CONTEXT = Path(__file__).parents[1] / "docs" / "base_context.md"


@dataclass(frozen=True)
class GeneralizationCase:
    case_id: str
    stem: str
    use_approved_context: bool = False

    @property
    def spec_path(self) -> Path:
        return FIXTURES / f"{self.stem}.md"

    @property
    def events_path(self) -> Path:
        return FIXTURES / f"{self.stem}.ndjson"


GENERALIZATION_CASES = (
    GeneralizationCase("leave_one_out", "01_leave_one_out"),
    GeneralizationCase("renamed_events", "02_renamed_events"),
    GeneralizationCase("unexpected_optional_field", "03_unexpected_optional_field"),
    GeneralizationCase("numeric_string", "04_numeric_string"),
    GeneralizationCase("missing_application_id", "05_missing_application_id"),
    GeneralizationCase("unexpected_nested_object", "06_unexpected_nested_object"),
    GeneralizationCase("uncomputable_metric", "07_uncomputable_metric"),
    GeneralizationCase("context_filtering", "08_document_verification", True),
    GeneralizationCase("multi_currency", "09_multi_currency"),
    GeneralizationCase("recipient_without_user", "10_recipient_without_user"),
)


async def _generate_case_async(case: GeneralizationCase) -> tuple[ContractGenerationResult, object]:
    settings = Settings()
    if not settings.llm_configured:
        raise AssertionError("the generalization matrix requires a configured live LLM provider")
    profile = SourceProfiler().profile(case.events_path)
    context_summary = None
    context_evidence_ids: list[str] = []
    if case.use_approved_context:
        bundle = build_base_context_bundle(BASE_CONTEXT)
        context_summary = json.dumps(bundle.projection, sort_keys=True, separators=(",", ":"))
        context_evidence_ids = bundle.projection["evidence_ids"]
    provider = OpenAICompatibleProvider(settings)
    try:
        result = await InstrumentationAgent(
            provider,
            context_max_chars=settings.contract_context_max_chars,
            total_timeout_seconds=settings.llm_total_generation_timeout_seconds,
        ).generate_contract(
            case.spec_path.read_text(encoding="utf-8"),
            profile,
            context_summary=context_summary,
            context_evidence_ids=context_evidence_ids,
        )
    finally:
        await provider.aclose()
    return result, profile


def run_generalization_case(
    case: GeneralizationCase,
) -> tuple[ContractGenerationResult, object]:
    return asyncio.run(_generate_case_async(case))


@pytest.mark.parametrize("case", GENERALIZATION_CASES, ids=lambda case: case.case_id)
def test_live_generalization_matrix(case: GeneralizationCase, record_property) -> None:
    result, profile = run_generalization_case(case)
    record_property("validation_status", result.validation_status)
    record_property("attempts", result.attempts)
    print(
        f"GENERALIZATION_RESULT case={case.case_id} "
        f"validation_status={result.validation_status} attempts={result.attempts}"
    )

    if case.case_id == "missing_application_id" and result.validation_status == "blocked":
        explanation = " ".join(
            f"{error.code} {error.path} {error.message}" for error in result.errors
        ).casefold()
        assert "key" in explanation or "identifier" in explanation or "entity" in explanation
        return

    assert result.validation_status == "valid", [error.model_dump() for error in result.errors]
    assert 1 <= result.attempts <= 4
    contract = result.analytics_contract
    assert contract is not None

    if case.case_id == "leave_one_out":
        assert contract.primary_entity == "group"
        assert contract.funnels[0].entity_key == "group_id"

    elif case.case_id == "renamed_events":
        observed_events = profile.event_names
        contract_events = {event.name for event in contract.events}
        assert contract_events == observed_events
        assert all(step.event_name in observed_events for step in contract.funnels[0].steps)
        assert not contract_events & {
            "express_checkout_shown",
            "express_checkout_selected",
            "saved_method_used",
            "otp_entered",
            "express_payment_confirmed",
        }

    elif case.case_id == "unexpected_optional_field":
        optional_field = next(
            field for field in contract.fields if field.source_path == "optional_rollout_bucket"
        )
        assert optional_field.spec_only is False
        assert optional_field.observed_null_rate == 0.0

    elif case.case_id == "numeric_string":
        mixed_field = next(field for field in profile.fields if field.path == "group_size")
        assert set(mixed_field.observed_types) == {JsonType.INTEGER, JsonType.STRING}
        assert (
            next(
                field for field in contract.fields if field.source_path == "group_size"
            ).semantic_type.value
            == "string"
        )

    elif case.case_id == "missing_application_id":
        primary = next(
            entity for entity in contract.entities if entity.name == contract.primary_entity
        )
        assert primary.field_path == "recovery_session_id"
        assert primary.field_path in profile.field_paths
        assert contract.funnels[0].entity_key == primary.field_path

    elif case.case_id == "unexpected_nested_object":
        contract_fields = {field.source_path for field in contract.fields}
        assert {
            "delivery_context",
            "delivery_context.client",
            "delivery_context.retry",
            "delivery_context.retry.count",
        } <= contract_fields

    elif case.case_id == "uncomputable_metric":
        assert any(
            question.classification.value in {"requires_external_context", "not_computable"}
            for question in contract.open_questions
        )

    elif case.case_id == "context_filtering":
        forbidden_references = {
            "destination_card_clicked",
            "application_started",
            "document_uploaded",
            "purchase_completed",
            "session_id",
        }
        executable_references = {
            *(step.event_name for funnel in contract.funnels for step in funnel.steps),
            *(entity.field_path for entity in contract.entities),
            *(metric.entity_key for metric in contract.metrics),
            *(relationship.from_field for relationship in contract.relationships),
            *(relationship.to_field for relationship in contract.relationships),
        }
        metric_expressions = " ".join(
            f"{metric.numerator} {metric.denominator}" for metric in contract.metrics
        )
        assert not forbidden_references & executable_references
        assert all(reference not in metric_expressions for reference in forbidden_references)
        assert {step.event_name for step in contract.funnels[0].steps} <= profile.event_names

    elif case.case_id == "multi_currency":
        assert profile.currency_fields[0].distinct_count >= 2
        currency_metrics = [
            metric for metric in contract.metrics if metric.value_type == MetricValueType.CURRENCY
        ]
        assert currency_metrics
        assert all(
            metric.currency_dimension or metric.fx_normalization_rule for metric in currency_metrics
        )

    elif case.case_id == "recipient_without_user":
        assert contract.primary_entity == "share"
        assert contract.funnels[0].entity_key == "share_id"
        user_field = next(field for field in profile.fields if field.path == "user_id")
        assert set(user_field.observed_in_events) == {"status_shared"}
