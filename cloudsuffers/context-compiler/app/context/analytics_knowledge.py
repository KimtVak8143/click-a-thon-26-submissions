from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.context.models import ContextSource

KNOWLEDGE_VERSION = "analytics_knowledge_v1"
_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANALYTICS_KNOWLEDGE_PATHS = (
    _ROOT / "docs" / "ANALYTICS_KNOWLEDGE_BASE.md",
    _ROOT / "docs" / "FEATURE_PATTERN_REFERENCE.md",
)
_KNOWLEDGE_NAMESPACE = uuid.UUID("65644dad-c3e2-5c93-af8e-08b5d41d9335")
_REQUIRED_MARKERS = {
    "ANALYTICS_KNOWLEDGE_BASE.md": (
        "# Product Analytics Knowledge Base",
        "## ClickHouse DDL review",
        "## Recommendation evidence rule",
    ),
    "FEATURE_PATTERN_REFERENCE.md": (
        "# Feature Pattern Reference Guide",
        "## Viral loop: Visa Status Sharing",
        "## Classification rules",
    ),
}


def load_analytics_knowledge(
    source_paths: Iterable[Path] = DEFAULT_ANALYTICS_KNOWLEDGE_PATHS,
) -> tuple[list[ContextSource], dict[str, Any]]:
    sources = [_load_source(path) for path in source_paths]
    source_checksums = {source.source_name: source.content_sha256 for source in sources}
    projection = _projection(source_checksums)
    projection["bundle_sha256"] = hashlib.sha256(
        json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return sources, projection


def classify_feature_pattern(event_names: Iterable[str], objective: str = "") -> dict[str, Any]:
    _, knowledge = load_analytics_knowledge()
    haystack = " ".join([*event_names, objective]).casefold()
    matches = []
    for pattern in knowledge["patterns"]:
        matched_signals = [signal for signal in pattern["signals"] if signal in haystack]
        if matched_signals:
            matches.append((len(matched_signals), pattern["pattern_id"], pattern, matched_signals))
    if not matches:
        fallback = next(
            pattern for pattern in knowledge["patterns"] if pattern["pattern_id"] == "multi_path"
        )
        return {**fallback, "matched_signals": [], "classification_confidence": "fallback"}
    _, _, selected, matched_signals = max(matches, key=lambda item: (item[0], item[1]))
    return {
        **selected,
        "matched_signals": sorted(matched_signals),
        "classification_confidence": "heuristic",
    }


def scope_analytics_knowledge(
    knowledge: dict[str, Any], event_names: Iterable[str], objective: str = ""
) -> dict[str, Any]:
    selected = classify_feature_pattern(event_names, objective)
    evidence_ids = set(knowledge.get("evidence_ids", []))
    selected_evidence = selected.get("evidence_id")
    if isinstance(selected_evidence, str):
        evidence_ids.add(selected_evidence)
    return {
        key: value for key, value in knowledge.items() if key not in {"patterns", "evidence_ids"}
    } | {
        "selected_pattern": selected,
        "evidence_ids": sorted(evidence_ids),
    }


def _load_source(path: Path) -> ContextSource:
    if not path.is_file():
        raise FileNotFoundError(f"analytics knowledge source does not exist: {path}")
    content = path.read_text(encoding="utf-8")
    markers = _REQUIRED_MARKERS.get(path.name)
    if markers is None or any(marker not in content for marker in markers):
        raise ValueError(f"analytics knowledge source is missing canonical sections: {path.name}")
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()
    try:
        relative_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        relative_path = path.name
    return ContextSource(
        source_id=uuid.uuid5(_KNOWLEDGE_NAMESPACE, f"source:{content_sha256}"),
        content_sha256=content_sha256,
        source_name=path.name,
        source_path=relative_path,
        source_kind="analytics_knowledge",
        parser_version=KNOWLEDGE_VERSION,
        source_content=content,
    )


def _projection(source_checksums: dict[str, str]) -> dict[str, Any]:
    universal_evidence = [
        "analytics_kb:v1:event_envelope",
        "analytics_kb:v1:data_quality",
        "analytics_kb:v1:funnel_analysis",
        "analytics_kb:v1:anomaly_detection",
        "analytics_kb:v1:ddl_review",
        "analytics_kb:v1:metric_formulas",
        "analytics_kb:v1:metric_quality",
        "analytics_kb:v1:segmentation",
        "analytics_kb:v1:analysis_workflow",
        "analytics_kb:v1:evidence_policy",
    ]
    patterns = [
        _pattern(
            "linear_funnel",
            ["shown", "selected", "started", "confirmed", "completed", "otp", "checkout"],
            ["step_conversion", "overall_conversion", "dropoff", "time_to_convert"],
            ["device_type", "os", "geoip_country_code"],
        ),
        _pattern(
            "iterative_loop",
            ["group", "traveller", "added", "removed", "submitted", "edit"],
            ["completion_rate", "iterations_per_entity", "add_remove_ratio", "abandonment"],
            ["group_size", "relation", "docs_complete"],
        ),
        _pattern(
            "viral_loop",
            ["share", "link", "recipient", "channel", "cta"],
            ["share_rate", "open_rate", "recipient_conversion", "k_factor"],
            ["channel", "status_shared", "recipient_is_new_user"],
        ),
        _pattern(
            "recovery_flow",
            ["abandon", "reminder", "resum", "reconvert", "drop_step"],
            ["delivery_rate", "open_rate", "click_rate", "recovery_rate", "time_to_recovery"],
            ["drop_step", "channel", "hours_since_drop"],
        ),
        _pattern(
            "upsell_flow",
            ["offer", "forex", "addon", "currency", "cart", "purchased"],
            ["attach_rate", "step_conversion", "addon_value", "aov_lift"],
            ["destination", "currency", "channel"],
        ),
        _pattern(
            "multi_path",
            ["path", "route", "option"],
            ["path_preference", "conversion_by_path"],
            ["device_type", "geoip_country_code", "user_type"],
        ),
    ]
    return {
        "version": "1.0",
        "parser_version": KNOWLEDGE_VERSION,
        "source_checksums": source_checksums,
        "event_envelope": [
            "event",
            "id",
            "timestamp",
            "user_id",
            "application_id",
            "device_type",
            "os",
            "app_version",
            "client_lib",
            "geoip_country_code",
        ],
        "data_quality_checks": [
            "expected_event_coverage",
            "event_sequence",
            "duplicate_event_identity",
            "identifier_relationships",
            "type_and_enum_conformance",
            "null_rate",
            "numeric_range",
            "utc_time_and_ordering",
            "schema_drift",
        ],
        "ddl_policy": {
            "dialect": "ClickHouse",
            "event_time_type": "DateTime64(3, 'UTC')",
            "partition_expression": "toYYYYMM(timestamp)",
            "sort_key_order": ["primary_workflow_key", "event_name", "timestamp"],
            "separate_raw_facts_from_metrics": True,
            "flatten_frequently_filtered_dimensions": True,
        },
        "metric_requirements": [
            "numerator",
            "denominator",
            "entity_grain",
            "time_window",
            "deduplication_policy",
            "zero_denominator_behavior",
            "currency_policy_when_relevant",
        ],
        "analysis_sequence": [
            "validate_data_quality",
            "classify_feature_pattern",
            "establish_baseline",
            "analyze_funnel_and_segments",
            "investigate_anomalies",
            "recommend_testable_action",
        ],
        "evidence_policy": {
            "numbers_require_sql_result": True,
            "knowledge_may_supply_numbers": False,
            "knowledge_may_establish_causality": False,
        },
        "patterns": patterns,
        "evidence_ids": sorted(
            [*universal_evidence, *(pattern["evidence_id"] for pattern in patterns)]
        ),
    }


def _pattern(
    pattern_id: str,
    signals: list[str],
    metrics: list[str],
    segments: list[str],
) -> dict[str, Any]:
    return {
        "pattern_id": pattern_id,
        "signals": signals,
        "recommended_metrics": metrics,
        "candidate_segments": segments,
        "evidence_id": f"feature_patterns:v1:{pattern_id}",
    }
