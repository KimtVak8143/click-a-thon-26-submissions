CREATE TABLE IF NOT EXISTS compiler_meta.context_issues
(
    issue_id UUID,
    context_version_id UUID,
    issue_code LowCardinality(String),
    category LowCardinality(String),
    severity LowCardinality(String),
    status LowCardinality(String),
    title String,
    description String,
    evidence_json String,
    resolution Nullable(String),
    detected_at DateTime64(3, 'UTC'),
    resolved_at Nullable(DateTime64(3, 'UTC')),
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(detected_at)
ORDER BY (issue_id)
