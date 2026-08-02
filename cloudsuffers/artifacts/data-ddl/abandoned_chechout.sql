CREATE TABLE IF NOT EXISTS `clickathon1`.`abandoned_checkout_recovery_events`
(
    id UUID,
    timestamp DateTime64(3, 'UTC'),
    _ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    app_version String,
    application_id String,
    channel Nullable(String),
    city String,
    client_lib String,
    destination String,
    device_type String,
    drop_step String,
    event String,
    geoip_country_code FixedString(2),
    hours_since_drop Nullable(Int64),
    os Nullable(String),
    user_id String,
    event_name LowCardinality(String) MATERIALIZED toLowCardinality(event)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (application_id, event_name, timestamp)
TTL timestamp + INTERVAL 730 DAY DELETE

CREATE MATERIALIZED VIEW IF NOT EXISTS `clickathon1`.`abandoned_checkout_recovery_funnel_daily_mv`
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (date, event_name)
AS SELECT
    toDate(timestamp) AS date,
    event_name,
    uniqExactState(application_id) AS unique_entity_count
FROM `clickathon1`.`abandoned_checkout_recovery_events`
GROUP BY date, event_name