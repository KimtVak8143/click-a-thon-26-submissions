CREATE TABLE IF NOT EXISTS {metadata_database}.baseline_metric_snapshots
(
    snapshot_id UUID,
    source_database String,
    source_fingerprint_sha256 FixedString(64),
    metrics_json String,
    evidence_ids_json String,
    computed_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (source_database, source_fingerprint_sha256, snapshot_id)
