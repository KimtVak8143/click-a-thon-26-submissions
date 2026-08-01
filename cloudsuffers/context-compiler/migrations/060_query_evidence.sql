CREATE TABLE IF NOT EXISTS {metadata_database}.query_evidence
(
    evidence_id UUID,
    run_id UUID,
    context_version_id UUID,
    metric_name LowCardinality(String),
    sql String,
    parameters_json String,
    result_json String,
    result_checksum FixedString(64),
    clickhouse_query_id String,
    latency_ms UInt64,
    executed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(executed_at)
ORDER BY (run_id, executed_at, evidence_id)
