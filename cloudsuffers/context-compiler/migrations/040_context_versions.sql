CREATE TABLE IF NOT EXISTS compiler_meta.context_versions
(
    context_version_id UUID,
    parent_context_version_id Nullable(UUID),
    schema_version_id UUID,
    version UInt64,
    status LowCardinality(String),
    snapshot_json String,
    diff_json String,
    published_at Nullable(DateTime64(3, 'UTC')),
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (version, context_version_id)
