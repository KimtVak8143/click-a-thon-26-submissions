CREATE TABLE IF NOT EXISTS {metadata_database}.context_sources
(
    source_id UUID,
    content_sha256 FixedString(64),
    source_name String,
    source_path String,
    source_kind LowCardinality(String),
    parser_version String,
    source_content String,
    created_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (content_sha256, source_id)
