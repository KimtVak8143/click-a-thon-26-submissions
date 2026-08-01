CREATE TABLE IF NOT EXISTS {metadata_database}.ai_judge_results
(
    recommendation_id String,
    trace_id String,
    score Float32,
    confidence Float32,
    reason String,
    model LowCardinality(String),
    prompt_name LowCardinality(String),
    prompt_version String,
    raw_output String,
    created_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (recommendation_id)
