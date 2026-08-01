CREATE TABLE IF NOT EXISTS {metadata_database}.context_relationships
(
    record_id UUID,
    context_version_id UUID,
    relationship_id LowCardinality(String),
    source_entity LowCardinality(String),
    target_entity LowCardinality(String),
    source_field String,
    target_field String,
    cardinality LowCardinality(String),
    temporal_constraint Nullable(String),
    evidence_ids_json String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (context_version_id, relationship_id, record_id)
