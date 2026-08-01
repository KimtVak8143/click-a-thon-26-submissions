CREATE TABLE IF NOT EXISTS {metadata_database}.ai_spans
(
    trace_id String,
    span_id String,
    parent_span_id String,
    name LowCardinality(String),
    kind LowCardinality(String),
    started_at DateTime64(6, 'UTC'),
    completed_at DateTime64(6, 'UTC'),
    latency_ms Float64,
    status_code UInt8,
    status_message String,
    input_json String,
    output_json String,
    metadata_json String,
    tokens_input UInt64,
    tokens_output UInt64,
    cost_usd Decimal64(9),
    error String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(started_at)
ORDER BY (trace_id, started_at, span_id)
TTL started_at + INTERVAL 365 DAY
