CREATE TABLE IF NOT EXISTS {metadata_database}.context_metrics
(
    record_id UUID,
    context_version_id UUID,
    metric_id LowCardinality(String),
    label String,
    definition_json String,
    computability LowCardinality(String),
    evidence_ids_json String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (context_version_id, metric_id, record_id)
