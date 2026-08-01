CREATE TABLE IF NOT EXISTS {metadata_database}.ai_evaluations
(
    recommendation_id String,
    trace_id String,
    evaluator LowCardinality(String),
    score Float32,
    passed UInt8,
    reason String,
    metadata_json String,
    created_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (recommendation_id, evaluator)
