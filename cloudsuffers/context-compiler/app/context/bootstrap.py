from __future__ import annotations

import argparse
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.clickhouse.client import build_clickhouse_client
from app.context.audit import base_context_issues, known_issue_hypotheses
from app.context.models import (
    ContextBootstrapBundle,
    ContextEntityRecord,
    ContextMetricRecord,
    ContextRelationshipRecord,
    ContextSource,
    ContextStatus,
    MetricComputability,
)
from app.context.repository import ClickHouseContextRepository, ContextRepositoryProtocol
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

PARSER_VERSION = "atlys_base_context_v1"
DEFAULT_BASE_CONTEXT_PATH = Path(__file__).resolve().parents[2] / "docs" / "base_context.md"
_CONTEXT_NAMESPACE = uuid.UUID("d1f77f52-48a8-5daa-842f-7ce493a9b3b1")
_REQUIRED_SOURCE_MARKERS = (
    "# Atlys Analytics — Base Context Layer",
    "destination_card_clicked  ->  application_started",
    "## 5. Known-issues log",
    "K7 — App 7.45 rollout",
)
logger = get_logger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    context_version_id: uuid.UUID
    content_sha256: str
    created: bool
    issue_count: int


class BaseContextBootstrapper:
    def __init__(self, repository: ContextRepositoryProtocol) -> None:
        self._repository = repository

    def bootstrap(self, source_path: Path = DEFAULT_BASE_CONTEXT_PATH) -> BootstrapResult:
        bundle = build_base_context_bundle(source_path)
        existing = self._repository.find_approved_by_content_sha256(bundle.source.content_sha256)
        if existing is not None:
            return BootstrapResult(
                context_version_id=existing.context_version_id,
                content_sha256=existing.content_sha256,
                created=False,
                issue_count=len(bundle.issues),
            )
        self._repository.persist_bootstrap(bundle)
        return BootstrapResult(
            context_version_id=bundle.context_version_id,
            content_sha256=bundle.source.content_sha256,
            created=True,
            issue_count=len(bundle.issues),
        )


def build_base_context_bundle(source_path: Path) -> ContextBootstrapBundle:
    if not source_path.is_file():
        raise FileNotFoundError(f"official base context does not exist: {source_path}")
    source_bytes = source_path.read_bytes()
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("official base context must be UTF-8") from exc
    missing = [marker for marker in _REQUIRED_SOURCE_MARKERS if marker not in source_text]
    if missing:
        raise ValueError("official base context is missing required canonical sections")

    content_sha256 = hashlib.sha256(source_bytes).hexdigest()
    source_id = uuid.uuid5(_CONTEXT_NAMESPACE, f"source:{content_sha256}")
    context_version_id = uuid.uuid5(_CONTEXT_NAMESPACE, f"context:v1:{content_sha256}")
    entities = _entities()
    metrics = _metrics()
    relationships = _relationships()
    issues = [*base_context_issues(), *known_issue_hypotheses()]
    evidence_ids = sorted(
        {
            "base_context:source",
            *(item for entity in entities for item in entity.evidence_ids),
            *(item for metric in metrics for item in metric.evidence_ids),
            *(item for relationship in relationships for item in relationship.evidence_ids),
            *(item for issue in issues for item in issue.evidence_ids),
        }
    )
    projection = {
        "source_precedence": [
            "observed_physical_schema_and_profile",
            "approved_context",
            "feature_specification",
            "llm_inference",
        ],
        "grain_policy": {
            "pre_application": ["session", "user"],
            "post_application_start": "application",
            "cross_session": "user",
            "new_workflow": "narrowest_stable_workflow_key",
            "generic_id_is_business_key": False,
        },
        "canonical_funnel": [
            "destination_card_clicked",
            "application_started",
            "document_uploaded",
            "purchase_completed",
        ],
        "supporting_events": [
            "search_typed",
            "landing_page_scrolled",
            "auth_completed",
            "pay_now_clicked",
        ],
        "entities": [item.model_dump(mode="json") for item in entities],
        "metrics": [item.model_dump(mode="json") for item in metrics],
        "relationships": [item.model_dump(mode="json") for item in relationships],
        "issues": [
            {
                "issue_code": item.issue_code,
                "category": item.category,
                "status": item.status,
                "hypothesis": item.hypothesis,
                "evidence_ids": item.evidence_ids,
            }
            for item in issues
        ],
        "evidence_ids": evidence_ids,
    }
    try:
        relative_path = source_path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        relative_path = source_path.name
    return ContextBootstrapBundle(
        context_version_id=context_version_id,
        version=1,
        status=ContextStatus.APPROVED,
        source=ContextSource(
            source_id=source_id,
            content_sha256=content_sha256,
            source_name=source_path.name,
            source_path=relative_path,
            source_kind="official_base_context",
            parser_version=PARSER_VERSION,
            source_content=source_text,
        ),
        projection=projection,
        entities=entities,
        metrics=metrics,
        relationships=relationships,
        issues=issues,
        changelog=[
            {
                "change_type": "bootstrap",
                "summary": "Imported official base context as approved version 1",
                "evidence_ids": ["base_context:source"],
            }
        ],
    )


def _entities() -> list[ContextEntityRecord]:
    return [
        ContextEntityRecord(
            entity_id="user",
            name="User",
            key_fields=["user_id"],
            grain_policy="cross-session person and cohort bridge; not a universal workflow key",
            evidence_ids=["base_context:entity:user"],
        ),
        ContextEntityRecord(
            entity_id="application",
            name="Application",
            key_fields=["application_id"],
            grain_policy="primary workflow grain at and after application_started",
            evidence_ids=["base_context:entity:application", "CTX-005"],
        ),
        ContextEntityRecord(
            entity_id="session",
            name="Session",
            key_fields=["session_id"],
            grain_policy="leadership conversion and pre-application browse grain",
            evidence_ids=["base_context:metric:conversion", "CTX-002"],
        ),
        ContextEntityRecord(
            entity_id="destination",
            name="Destination",
            key_fields=["destination"],
            grain_policy="ISO-2 analytical dimension; region mapping unresolved",
            evidence_ids=["base_context:entity:destination", "CTX-007"],
        ),
        ContextEntityRecord(
            entity_id="document",
            name="Document",
            key_fields=["application_id"],
            grain_policy="document upload attempt within an application",
            evidence_ids=["base_context:entity:document", "CTX-010"],
        ),
    ]


def _metric(
    metric_id: str,
    label: str,
    numerator: str,
    denominator: str,
    grain: str,
    key: str,
    *,
    dimensions: list[str],
    computability: MetricComputability = MetricComputability.COMPUTABLE,
    currency_policy: str | None = None,
    evidence_ids: list[str],
) -> ContextMetricRecord:
    return ContextMetricRecord(
        metric_id=metric_id,
        label=label,
        numerator=numerator,
        denominator=denominator,
        entity_grain=grain,
        entity_key=key,
        time_attribution="ordered event time in UTC within the declared analysis window",
        analysis_window="caller-declared bounded window",
        dimensions=dimensions,
        zero_denominator_behavior="null",
        deduplication_policy="deduplicate by source event identity; stage entities count once",
        currency_policy=currency_policy,
        computability=computability,
        evidence_ids=evidence_ids,
    )


def _metrics() -> list[ContextMetricRecord]:
    common_dimensions = ["device_type", "os", "geoip_country_code", "destination"]
    return [
        _metric(
            "session_purchase_conversion_rate",
            "Session purchase conversion rate",
            "distinct eligible sessions with purchase_completed",
            "eligible distinct sessions",
            "session",
            "session_id",
            dimensions=common_dimensions,
            evidence_ids=["base_context:metric:conversion", "CTX-002"],
        ),
        _metric(
            "application_purchase_conversion_rate",
            "Application purchase conversion rate",
            "distinct applications reaching purchase_completed after start",
            "distinct applications reaching application_started",
            "application",
            "application_id",
            dimensions=common_dimensions,
            evidence_ids=["base_context:metric:funnel_conversion", "CTX-002", "CTX-005"],
        ),
        _metric(
            "stage_dropoff_rate",
            "Stage drop-off rate",
            "current-stage ordered entities minus next-stage ordered entities",
            "current-stage ordered entities",
            "stage_specific",
            "application_id_or_session_id",
            dimensions=common_dimensions,
            evidence_ids=["base_context:metric:drop_off"],
        ),
        _metric(
            "stage_step_through_rate",
            "Stage step-through rate",
            "next-stage ordered entities",
            "current-stage ordered entities",
            "stage_specific",
            "application_id_or_session_id",
            dimensions=common_dimensions,
            evidence_ids=["base_context:metric:step_through"],
        ),
        _metric(
            "passport_capture_pass_rate",
            "Passport capture pass rate",
            "document uploads where failure-threshold flag is false",
            "deduplicated document uploads",
            "document_upload",
            "application_id",
            dimensions=["device_type", "os", "geoip_country_code", "capture_mode"],
            evidence_ids=["base_context:metric:passport_capture", "CTX-010"],
        ),
        _metric(
            "revenue_per_conversion",
            "Revenue per conversion",
            "sum or average purchase_completed.value",
            "deduplicated purchase conversions",
            "application",
            "application_id",
            dimensions=[*common_dimensions, "currency"],
            currency_policy="group by currency unless an approved FX rule is supplied",
            evidence_ids=["base_context:metric:revenue", "CTX-004"],
        ),
        _metric(
            "on_time_delivery_rate",
            "On-time delivery rate",
            "applications issued by promised ETA",
            "issued applications",
            "fulfilment_application",
            "application_id",
            dimensions=common_dimensions,
            computability=MetricComputability.EXTERNAL,
            evidence_ids=["base_context:metric:on_time_delivery", "CTX-001", "CTX-003"],
        ),
        _metric(
            "pay_click_purchase_rate",
            "Pay-click purchase rate",
            "ordered purchase completions after pay_now_clicked",
            "eligible pay clicks or applications",
            "application_payment_attempt",
            "application_id",
            dimensions=[*common_dimensions, "payment_method"],
            evidence_ids=["base_context:hypothesis:K1", "CTX-005", "CTX-010"],
        ),
    ]


def _relationships() -> list[ContextRelationshipRecord]:
    return [
        ContextRelationshipRecord(
            relationship_id="user_to_application",
            source_entity="user",
            target_entity="application",
            source_field="user_id",
            target_field="user_id",
            cardinality="one_to_many",
            temporal_constraint="application begins at application_started",
            evidence_ids=["base_context:relationship:user_application"],
        ),
        ContextRelationshipRecord(
            relationship_id="application_to_document",
            source_entity="application",
            target_entity="document",
            source_field="application_id",
            target_field="application_id",
            cardinality="one_to_many",
            temporal_constraint="timestamp ascending within application_id",
            evidence_ids=["base_context:relationship:application_document", "CTX-010"],
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap the official approved base context")
    parser.add_argument("--source", type=Path, default=DEFAULT_BASE_CONTEXT_PATH)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    repository = ClickHouseContextRepository(
        lambda: build_clickhouse_client(settings), settings.clickhouse_metadata_database
    )
    try:
        result = BaseContextBootstrapper(repository).bootstrap(args.source)
    finally:
        repository.close()
    logger.info(
        "base_context_bootstrap_complete",
        extra={
            "context_version_id": str(result.context_version_id),
            "content_sha256": result.content_sha256,
            "context_created": result.created,
            "issue_count": result.issue_count,
        },
    )


if __name__ == "__main__":
    main()
