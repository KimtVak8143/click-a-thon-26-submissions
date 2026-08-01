CREATE TABLE IF NOT EXISTS {metadata_database}.ai_traces
(
    trace_id String,
    name LowCardinality(String),
    started_at DateTime64(6, 'UTC'),
    completed_at DateTime64(6, 'UTC'),
    latency_ms Float64,
    status_code UInt8,
    input_json String,
    output_json String,
    metadata_json String,
    total_tokens UInt64,
    total_cost_usd Decimal64(9),
    error String
)
ENGINE = ReplacingMergeTree(completed_at)
PARTITION BY toYYYYMM(started_at)
ORDER BY (trace_id)
TTL started_at + INTERVAL 365 DAY
