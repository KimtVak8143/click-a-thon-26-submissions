CREATE TABLE IF NOT EXISTS {metadata_database}.context_entities
(
    record_id UUID,
    context_version_id UUID,
    entity_id LowCardinality(String),
    name String,
    key_fields_json String,
    grain_policy String,
    evidence_ids_json String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (context_version_id, entity_id, record_id)
