from app.context.analytics_knowledge import (
    classify_feature_pattern,
    load_analytics_knowledge,
    scope_analytics_knowledge,
)


def test_knowledge_sources_are_versioned_and_checksum_backed() -> None:
    sources, knowledge = load_analytics_knowledge()

    assert len(sources) == 2
    assert all(len(source.content_sha256) == 64 for source in sources)
    assert len(knowledge["bundle_sha256"]) == 64
    assert knowledge["ddl_policy"]["dialect"] == "ClickHouse"
    assert knowledge["evidence_policy"]["numbers_require_sql_result"] is True


def test_feature_pattern_classifier_recognizes_cross_user_viral_flow() -> None:
    pattern = classify_feature_pattern(
        ["share_clicked", "link_generated", "link_opened", "recipient_cta_clicked"]
    )

    assert pattern["pattern_id"] == "viral_loop"
    assert pattern["evidence_id"] == "feature_patterns:v1:viral_loop"


def test_scoped_knowledge_keeps_only_selected_pattern() -> None:
    _, knowledge = load_analytics_knowledge()

    scoped = scope_analytics_knowledge(
        knowledge,
        ["abandonment_detected", "reminder_sent", "resumed_at_step", "reconverted"],
    )

    assert "patterns" not in scoped
    assert scoped["selected_pattern"]["pattern_id"] == "recovery_flow"
    assert "feature_patterns:v1:recovery_flow" in scoped["evidence_ids"]
