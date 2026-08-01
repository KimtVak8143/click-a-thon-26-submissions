from __future__ import annotations

from app.context.models import ContextIssueRecord


def base_context_issues() -> list[ContextIssueRecord]:
    """Return the deterministic audit findings required by the approved alignment plan."""

    definitions = [
        (
            "CTX-001",
            "schema_contradiction",
            "ETA field contradiction",
            "The context claims visa_issuance_eta_days while the observed application event "
            "documents eta_shown with unresolved type and meaning.",
            ["base_context:entity:application", "physical_schema:application_started"],
            ["application_started", "on_time_delivery_rate"],
        ),
        (
            "CTX-002",
            "metric_ambiguity",
            "Conversion denominator ambiguity",
            "Leadership conversion uses sessions while funnel conversion uses application "
            "starters; a bare conversion_rate is not approved.",
            ["base_context:metric:conversion", "base_context:metric:funnel_conversion"],
            ["session_purchase_conversion_rate", "application_purchase_conversion_rate"],
        ),
        (
            "CTX-003",
            "unavailable_data",
            "On-time delivery inputs unavailable",
            "Issuance status and issuance timestamps live outside the eight source tables.",
            ["base_context:scope:post_purchase", "base_context:metric:on_time_delivery"],
            ["on_time_delivery_rate"],
        ),
        (
            "CTX-004",
            "currency_risk",
            "Revenue is event-currency denominated",
            "Revenue must be grouped by currency or use an approved FX-normalization rule.",
            ["base_context:metric:revenue_per_conversion"],
            ["purchase_completed.value", "purchase_completed.currency"],
        ),
        (
            "CTX-005",
            "grain_risk",
            "The journey requires hybrid grain",
            "Pre-application events use session/user grain; post-start workflow analysis "
            "uses application grain, and user_id is not a universal workflow key.",
            ["base_context:entity:user", "base_context:entity:application"],
            ["funnels", "metrics"],
        ),
        (
            "CTX-006",
            "physical_optimization_debt",
            "Legacy event tables use id-first sorting",
            "Legacy ORDER BY (id, timestamp, user_id) is misaligned with time and segment queries.",
            ["base_context:instrumentation_note"],
            ["existing_event_tables"],
        ),
        (
            "CTX-007",
            "missing_mapping",
            "Destination-region mapping is unavailable",
            "The region taxonomy has no authoritative mapping table and must remain unresolved.",
            ["base_context:entity:destination"],
            ["destination_region"],
        ),
        (
            "CTX-008",
            "dimension_normalization",
            "Canonical dimension normalization is unresolved",
            "Device, OS, geo, destination, and app-version values may differ by source.",
            ["base_context:segment_cuts"],
            ["device_type", "os", "geoip_country_code", "destination", "app_version"],
        ),
        (
            "CTX-009",
            "time_semantics",
            "Timezone and late-arrival policy is incomplete",
            "Event ordering is defined but client timezone and late-arrival handling are not.",
            ["base_context:funnel_order"],
            ["event_time", "attribution_windows"],
        ),
        (
            "CTX-010",
            "duplicate_retry_semantics",
            "Duplicate and retry semantics are unresolved",
            "SDK retries and repeated interactions can overcount funnel stages without an "
            "event-identity and deduplication policy.",
            ["base_context:event", "base_context:known_issues"],
            ["event_id", "funnels", "metrics"],
        ),
    ]
    return [
        ContextIssueRecord(
            issue_code=code,
            category=category,
            severity="warning",
            status="open",
            title=title,
            description=description,
            evidence_ids=evidence,
            affected_artifacts=affected,
        )
        for code, category, title, description, evidence, affected in definitions
    ]


def known_issue_hypotheses() -> list[ContextIssueRecord]:
    titles = {
        "K1": "iOS WebKit OTP autofill regression",
        "K2": "Passport scan model update",
        "K3": "MRZ OCR weaker on non-Latin passports",
        "K4": "Schengen summer slot scarcity",
        "K5": "WhatsApp nudge launch",
        "K6": "SUMMER20 coupon campaign",
        "K7": "App 7.45 rollout",
    }
    return [
        ContextIssueRecord(
            issue_code=code,
            category="hypothesis",
            severity="info",
            status="open",
            title=title,
            description=(
                "Context-supplied hypothesis; it is not a proven cause until"
                " supported by computed evidence."
            ),
            evidence_ids=[f"base_context:hypothesis:{code}"],
            affected_artifacts=["analytics_hypotheses"],
            hypothesis=True,
        )
        for code, title in titles.items()
    ]
