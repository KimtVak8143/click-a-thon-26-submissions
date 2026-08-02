CREATE TABLE IF NOT EXISTS `clickathon1`.`express_checkout_events`
(
    id UUID,
    timestamp DateTime64(3, 'UTC'),
    _ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    app_version String,
    application_id String,
    city String,
    client_lib String,
    currency Nullable(String),
    destination String,
    device_type String,
    eligible Nullable(Bool),
    event String,
    geoip_country_code FixedString(2),
    os Nullable(String),
    otp_attempts Nullable(Int64),
    otp_success Nullable(Bool),
    payment Nullable(String),
    payment_amount Nullable(Float64),
    payment_currency Nullable(String),
    payment_latency_ms Nullable(Int64),
    saved_method_type Nullable(String),
    shown_amount Nullable(Float64),
    user_id String,
    event_name LowCardinality(String) MATERIALIZED toLowCardinality(event)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (application_id, event_name, timestamp)
TTL timestamp + INTERVAL 730 DAY DELETE

CREATE MATERIALIZED VIEW IF NOT EXISTS `clickathon1`.`express_checkout_funnel_daily_mv`
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (date, event_name)
AS SELECT
    toDate(timestamp) AS date,
    event_name,
    uniqExactState(application_id) AS unique_entity_count
FROM `clickathon1`.`express_checkout_events`
GROUP BY date, event_name