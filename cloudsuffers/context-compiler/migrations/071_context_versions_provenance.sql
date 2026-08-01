ALTER TABLE {metadata_database}.context_versions
    ADD COLUMN IF NOT EXISTS source_id Nullable(UUID),
    ADD COLUMN IF NOT EXISTS content_sha256 FixedString(64),
    ADD COLUMN IF NOT EXISTS parser_version String
