CREATE TABLE IF NOT EXISTS `clickathon1`.`group_family_applications_events`
(
    id UUID,
    timestamp DateTime64(3, 'UTC'),
    _ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    app_version String,
    application_id String,
    city String,
    client_lib String,
    destination String,
    device_type String,
    docs_complete Nullable(Bool),
    event String,
    geoip_country_code FixedString(2),
    group_id String,
    group_size Int64,
    os Nullable(String),
    relation Nullable(String),
    traveller_index Nullable(Int64),
    travellers_submitted Nullable(Int64),
    user_id String,
    event_name LowCardinality(String) MATERIALIZED toLowCardinality(event)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (group_id, event_name, timestamp)
TTL timestamp + INTERVAL 730 DAY DELETE

CREATE MATERIALIZED VIEW IF NOT EXISTS `clickathon1`.`group_family_applications_funnel_daily_mv`
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (date, event_name)
AS SELECT
    toDate(timestamp) AS date,
    event_name,
    uniqExactState(group_id) AS unique_entity_count
FROM `clickathon1`.`group_family_applications_events`
GROUP BY date, event_name