import hashlib
from pathlib import Path

from app.context.audit import base_context_issues, known_issue_hypotheses
from app.context.bootstrap import BaseContextBootstrapper, build_base_context_bundle
from app.context.models import ApprovedContext
from app.context.repository import InMemoryContextRepository

BASE_CONTEXT = Path(__file__).parents[1] / "docs" / "base_context.md"


def test_bootstrap_is_idempotent_and_preserves_exact_source_bytes() -> None:
    repository = InMemoryContextRepository()
    bootstrapper = BaseContextBootstrapper(repository)

    first = bootstrapper.bootstrap(BASE_CONTEXT)
    second = bootstrapper.bootstrap(BASE_CONTEXT)

    expected_hash = hashlib.sha256(BASE_CONTEXT.read_bytes()).hexdigest()
    assert first.created is True
    assert second.created is False
    assert first.context_version_id == second.context_version_id
    assert first.content_sha256 == second.content_sha256 == expected_hash
    assert len(repository.contexts) == 1
    assert len(repository.bundles) == 1
    assert repository.bundles[0].source.source_content.encode() == BASE_CONTEXT.read_bytes()


def test_latest_approved_context_selects_highest_version() -> None:
    bundle = build_base_context_bundle(BASE_CONTEXT)
    first = ApprovedContext(
        context_version_id=bundle.context_version_id,
        version=1,
        content_sha256=bundle.source.content_sha256,
        source_id=bundle.source.source_id,
        parser_version=bundle.source.parser_version,
        projection=bundle.projection,
    )
    second = first.model_copy(update={"version": 2})
    repository = InMemoryContextRepository([second, first])

    assert repository.latest_approved() == second


def test_context_audit_contains_all_required_issue_classes_and_hypotheses() -> None:
    issues = base_context_issues()
    hypotheses = known_issue_hypotheses()

    assert {item.issue_code for item in issues} == {f"CTX-{index:03d}" for index in range(1, 11)}
    assert {item.category for item in issues} == {
        "schema_contradiction",
        "metric_ambiguity",
        "unavailable_data",
        "currency_risk",
        "grain_risk",
        "physical_optimization_debt",
        "missing_mapping",
        "dimension_normalization",
        "time_semantics",
        "duplicate_retry_semantics",
    }
    assert {item.issue_code for item in hypotheses} == {f"K{index}" for index in range(1, 8)}
    assert all(item.hypothesis and item.category == "hypothesis" for item in hypotheses)
    assert all("not a proven cause" in item.description for item in hypotheses)


def test_canonical_metric_registry_is_denominator_qualified_and_complete() -> None:
    bundle = build_base_context_bundle(BASE_CONTEXT)
    metrics = {item.metric_id: item for item in bundle.metrics}

    assert set(metrics) == {
        "session_purchase_conversion_rate",
        "application_purchase_conversion_rate",
        "stage_dropoff_rate",
        "stage_step_through_rate",
        "passport_capture_pass_rate",
        "revenue_per_conversion",
        "on_time_delivery_rate",
        "pay_click_purchase_rate",
    }
    assert "conversion_rate" not in metrics
    assert metrics["on_time_delivery_rate"].computability.value == "external"
    assert metrics["revenue_per_conversion"].currency_policy is not None
    for metric in metrics.values():
        assert metric.numerator
        assert metric.denominator
        assert metric.entity_grain
        assert metric.entity_key
        assert metric.time_attribution
        assert metric.analysis_window
        assert metric.deduplication_policy
        assert metric.evidence_ids
