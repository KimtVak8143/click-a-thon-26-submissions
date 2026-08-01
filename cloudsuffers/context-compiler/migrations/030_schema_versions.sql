CREATE TABLE IF NOT EXISTS {metadata_database}.schema_versions
(
    schema_version_id UUID,
    run_id UUID,
    contract_id UUID,
    feature_slug LowCardinality(String),
    version UInt32,
    database_name String,
    object_inventory_json String,
    ddl String,
    deployed_at Nullable(DateTime64(3, 'UTC')),
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (feature_slug, version, schema_version_id)
