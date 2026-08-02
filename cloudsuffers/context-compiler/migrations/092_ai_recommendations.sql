CREATE TABLE IF NOT EXISTS {metadata_database}.ai_recommendations
(
    recommendation_id String,
    trace_id String,
    status LowCardinality(String),
    question String,
    recommendation String,
    confidence Float32,
    prompt_name LowCardinality(String),
    prompt_version String,
    model_provider LowCardinality(String),
    model_name LowCardinality(String),
    model_version String,
    sql String,
    evidence_ids Array(String),
    created_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (recommendation_id)
