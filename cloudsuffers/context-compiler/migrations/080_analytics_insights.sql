CREATE TABLE IF NOT EXISTS {metadata_database}.analytics_insights
(
    insight_id UUID,
    run_id UUID,
    feature_slug LowCardinality(String),
    context_version_id UUID,
    title String,
    summary String,
    confidence Float32,
    category LowCardinality(String),
    evidence_ids_json String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (feature_slug, created_at, insight_id)
