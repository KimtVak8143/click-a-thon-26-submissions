CREATE TABLE IF NOT EXISTS `clickathon1`.`instant_forex_add_on_events`
(
    id UUID,
    timestamp DateTime64(3, 'UTC'),
    _ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    addon_value_inr Nullable(Float64),
    amount Nullable(Int64),
    app_version String,
    application_id String,
    city String,
    client_lib String,
    destination String,
    device_type String,
    event String,
    from_currency String,
    fx_rate Nullable(Float64),
    geoip_country_code FixedString(2),
    os Nullable(String),
    to_currency String,
    user_id String,
    event_name LowCardinality(String) MATERIALIZED toLowCardinality(event)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (application_id, event_name, timestamp)
TTL timestamp + INTERVAL 730 DAY DELETE

CREATE MATERIALIZED VIEW IF NOT EXISTS `clickathon1`.`instant_forex_add_on_funnel_daily_mv`
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (date, event_name)
AS SELECT
    toDate(timestamp) AS date,
    event_name,
    uniqExactState(application_id) AS unique_entity_count
FROM `clickathon1`.`instant_forex_add_on_events`
GROUP BY date, event_name