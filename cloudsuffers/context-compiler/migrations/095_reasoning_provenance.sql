CREATE TABLE IF NOT EXISTS {metadata_database}.reasoning_provenance
(
    recommendation_id String,
    trace_id String,
    status LowCardinality(String),
    spec_version_id String,
    spec_checksum FixedString(64),
    schema_version_id String,
    schema_checksum FixedString(64),
    context_version_id String,
    context_checksum FixedString(64),
    prompt_name LowCardinality(String),
    prompt_version String,
    model_provider LowCardinality(String),
    model_name LowCardinality(String),
    model_version String,
    sql String,
    evidence_ids Array(String),
    evaluations_json String,
    judge_json String,
    input_checksum FixedString(64),
    output_checksum FixedString(64),
    provenance_json String,
    created_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (recommendation_id)
