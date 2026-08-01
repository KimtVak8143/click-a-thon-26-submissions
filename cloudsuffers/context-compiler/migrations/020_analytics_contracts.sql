CREATE TABLE IF NOT EXISTS compiler_meta.analytics_contracts
(
    contract_id UUID,
    run_id UUID,
    feature_slug LowCardinality(String),
    contract_version String,
    status LowCardinality(String),
    contract_json String,
    spec_sha256 FixedString(64),
    events_sha256 FixedString(64),
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (feature_slug, created_at, contract_id)
