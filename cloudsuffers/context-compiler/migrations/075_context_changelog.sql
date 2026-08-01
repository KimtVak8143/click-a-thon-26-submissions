CREATE TABLE IF NOT EXISTS {metadata_database}.context_changelog
(
    change_id UUID,
    context_version_id UUID,
    sequence UInt32,
    change_type LowCardinality(String),
    summary String,
    evidence_ids_json String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (context_version_id, sequence, change_id)
