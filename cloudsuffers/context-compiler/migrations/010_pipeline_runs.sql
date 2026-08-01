CREATE TABLE IF NOT EXISTS compiler_meta.pipeline_runs
(
    run_id UUID,
    feature_slug LowCardinality(String),
    status LowCardinality(String),
    spec_sha256 Nullable(FixedString(64)),
    events_sha256 Nullable(FixedString(64)),
    contract_id Nullable(UUID),
    schema_version_id Nullable(UUID),
    context_version_id Nullable(UUID),
    langfuse_trace_id Nullable(String),
    error_message Nullable(String),
    started_at DateTime64(3, 'UTC'),
    completed_at Nullable(DateTime64(3, 'UTC')),
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(started_at)
ORDER BY (run_id)
