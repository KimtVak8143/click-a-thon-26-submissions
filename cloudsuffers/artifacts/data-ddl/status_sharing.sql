CREATE TABLE IF NOT EXISTS `clickathon1`.`visa_status_sharing_events`
(
    id UUID,
    timestamp DateTime64(3, 'UTC'),
    _ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    app_version Nullable(String),
    application_id Nullable(String),
    channel Nullable(String),
    city Nullable(String),
    client_lib Nullable(String),
    cta Nullable(String),
    destination String,
    device_type Nullable(String),
    event String,
    geoip_country_code Nullable(FixedString(2)),
    os Nullable(String),
    recipient_is_new_user Nullable(Bool),
    share_id String,
    status_shared Nullable(String),
    user_id Nullable(String),
    event_name LowCardinality(String) MATERIALIZED toLowCardinality(event)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (share_id, event_name, timestamp)
TTL timestamp + INTERVAL 730 DAY DELETE

CREATE MATERIALIZED VIEW IF NOT EXISTS `clickathon1`.`visa_status_sharing_funnel_daily_mv`
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (date, event_name)
AS SELECT
    toDate(timestamp) AS date,
    event_name,
    uniqExactState(share_id) AS unique_entity_count
FROM `clickathon1`.`visa_status_sharing_events`
GROUP BY date, event_name