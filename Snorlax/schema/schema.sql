-- #####################################################################
-- schema.sql — EVERYTHING the live app needs: tables + dictionary + view + MVs.
-- Run once. Then point ClickPipes (Redpanda → events_incoming) and read
-- live insights with ui_queries.sql (a.k.a. 04_ui_queries.sql).
--
--   clickhouse client --host <h> --user default --secure --queries-file schema.sql
--
-- Flow: Redpanda → ClickPipes → events_incoming ─(MV)→ events_raw
--        ─(MV: state machine)→ session_intervals ─(MV: hot)→ concurrency_hot_abs
--        + cold compaction → concurrency_cold_abs ; concurrency_now = union.
--
-- ===== LIVE-TRAFFIC TUNING (this is a continuous, high-throughput service) =====
-- INGEST (throughput):
--   * events_incoming = Null → no landing storage; MV converts on insert.
--   * ClickPipes: use LARGE batches (e.g. 100k rows / few seconds) — few big
--     inserts >> many small ones. Enable async inserts on the pipe if available.
--   * events_raw partitioned monthly + 30d TTL so raw never grows unbounded
--     (the aggregates live in cold_abs).
-- COMPUTE (bounded, never rescans history):
--   * Derivation window = 20 min, hot window = 10 min, freeze horizon = 10 min.
--     Recompute cost ∝ (window × ACTIVE sessions), independent of total history.
--     Keep windows as TIGHT as p99 heartbeat lag allows (measure via ClickStack).
--   * Refresh cadence: hot 30 s (freshness) · derivation 1 min · compaction 1 min.
--     Raise cadence if refresh time approaches the interval (watch REFRESH duration).
--   * Cold is append-only forward-fill → finalized minutes are never recomputed.
-- SERVE (fast under write load):
--   * Serving tables are absolute per (dims,minute) → queries are filter→sum→max/avg,
--     tiny reads, no cumsum. content_dict avoids JOINs on the hot path.
-- SCALE-OUT (100×): shard by video_session_id; size the service to peak concurrency
--   not event volume; if a single refresh can't finish in its interval, split the
--   hot window across parallel refreshes or move to per-session incremental state.
-- ===============================================================================
-- #####################################################################

CREATE DATABASE IF NOT EXISTS sonyliv_concurrency;

-- =====================================================================
-- A. TABLES
-- =====================================================================

-- ClickPipes landing (Redpanda JSON; ms epochs). Transient.
-- PRODUCER: one JSON message per event (JSONEachRow), keys = these columns,
--   Redpanda key = video_session_id. Do NOT pre-convert timestamps (send ms ints).
-- CLICKPIPES: Source=Kafka(Redpanda) · Topic=sonyliv.events · Format=JSONEachRow
--   · Destination=sonyliv_concurrency.events_incoming · map by field name.
CREATE TABLE IF NOT EXISTS sonyliv_concurrency.events_incoming
(
    content_id UInt64, video_session_id String, user_id String,
    event_type LowCardinality(String), event LowCardinality(String), event_timestamp UInt64,
    platform LowCardinality(String), app_version LowCardinality(String), country LowCardinality(String),
    audio_language LowCardinality(String), subtitle_language LowCardinality(String),
    player_version LowCardinality(String), session_start_epoch UInt64
)
ENGINE = Null;   -- MV consumes each insert; nothing stored (leaner). To DEBUG the
                 -- ClickPipes mapping, temporarily use: ENGINE = MergeTree ORDER BY tuple().

-- Canonical typed events.
CREATE TABLE IF NOT EXISTS sonyliv_concurrency.events_raw
(
    video_session_id String, user_id String, content_id UInt64,
    event_type Enum8('VideoSessionStart'=1,'VideoPlay'=2,'VideoHeartbeat'=3,
                     'AppBackgrounded'=4,'AppForegrounded'=5,'VideoSessionEnd'=6,'VideoError'=7,
                     'VideoPause'=8,'AdBreakStart'=9,'VideoSeek'=10),
    event LowCardinality(String),
    event_timestamp DateTime64(3,'UTC') CODEC(DoubleDelta, ZSTD(1)),
    session_start_epoch DateTime64(3,'UTC') CODEC(DoubleDelta, ZSTD(1)),
    platform LowCardinality(String), app_version LowCardinality(String), country LowCardinality(String),
    audio_language LowCardinality(String), subtitle_language LowCardinality(String), player_version LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY (video_session_id, event_timestamp)   -- session-contiguous: derivation is a streaming per-session read
PARTITION BY toYYYYMM(event_timestamp)         -- monthly (lifecycle only; bounded partition count)
TTL toDateTime(event_timestamp) + INTERVAL 30 DAY;  -- aggregates live in cold_abs; bound raw growth

-- Content metadata.
CREATE TABLE IF NOT EXISTS sonyliv_concurrency.content_dim
( content_id UInt64, title String, video_type LowCardinality(String), category LowCardinality(String) )
ENGINE = ReplacingMergeTree ORDER BY content_id;

-- Truly-active intervals (state-machine output).
CREATE TABLE IF NOT EXISTS sonyliv_concurrency.session_intervals
(
    video_session_id String, interval_idx UInt16,
    active_start DateTime64(3,'UTC'), active_end DateTime64(3,'UTC'),
    is_provisional UInt8 DEFAULT 0,
    content_id UInt64, platform LowCardinality(String), country LowCardinality(String),
    video_type LowCardinality(String), category LowCardinality(String), version UInt64
)
ENGINE = ReplacingMergeTree(version) ORDER BY (video_session_id, interval_idx);

-- Serving tiers: ABSOLUTE concurrency per (dims, minute).
-- cold = ReplacingMergeTree so a re-fired/retried compaction can't double-count
-- a minute (one row per (dims,minute) key; read with FINAL). hot is REPLACE-
-- recomputed wholesale, so plain MergeTree is fine there.
CREATE TABLE IF NOT EXISTS sonyliv_concurrency.concurrency_cold_abs
( country LowCardinality(String), platform LowCardinality(String), video_type LowCardinality(String),
  category LowCardinality(String), minute DateTime('UTC'), content_id UInt64, concurrent UInt32 )
ENGINE = ReplacingMergeTree ORDER BY (country, platform, video_type, category, minute, content_id);

CREATE TABLE IF NOT EXISTS sonyliv_concurrency.concurrency_hot_abs
( country LowCardinality(String), platform LowCardinality(String), video_type LowCardinality(String),
  category LowCardinality(String), minute DateTime('UTC'), content_id UInt64, concurrent UInt32 )
ENGINE = MergeTree ORDER BY (country, platform, video_type, category, minute, content_id);

-- EXTENDED drill-down serving table: the 4 high-cardinality dims
-- (app_version, player_version, audio_language, subtitle_language) IN ADDITION to
-- the core dims. Kept SEPARATE from the lean core tiers (PLAN §9 Fix #7) so the
-- common-case dashboard stays fast on the small core key, while drill-down
-- queries that filter on device/language read this. Absolute per (dims, minute),
-- same model. Populated by approach_extended_dims.sql from session_intervals
-- (offline/scheduled; no live hot/cold tier for the extended path in this version).
-- ORDER BY keeps the core key as its PREFIX (country,platform,video_type,category)
-- then adds the extended dims low→high cardinality, so a core-only filter still
-- uses the leading key, and this table is a clean superset of the core key.
CREATE TABLE IF NOT EXISTS sonyliv_concurrency.concurrency_ext_abs
( country LowCardinality(String), platform LowCardinality(String), video_type LowCardinality(String),
  category LowCardinality(String), subtitle_language LowCardinality(String),
  audio_language LowCardinality(String), player_version LowCardinality(String),
  app_version LowCardinality(String), minute DateTime('UTC'), content_id UInt64, concurrent UInt32 )
ENGINE = MergeTree
ORDER BY (country, platform, video_type, category, subtitle_language, audio_language, player_version, app_version, minute, content_id);

-- =====================================================================
-- B. DICTIONARY (content enrichment via dictGet)
-- =====================================================================
DROP DICTIONARY IF EXISTS sonyliv_concurrency.content_dict;
CREATE DICTIONARY sonyliv_concurrency.content_dict
( content_id UInt64, title String, video_type String, category String )
PRIMARY KEY content_id
SOURCE(CLICKHOUSE( USER 'default' PASSWORD '' DB 'sonyliv_concurrency' TABLE 'content_dim' ))
LAYOUT(HASHED()) LIFETIME(MIN 600 MAX 1200);

-- =====================================================================
-- C. SERVING VIEW (what UI reads) — cold ∪ hot, disjoint by minute
-- =====================================================================
CREATE OR REPLACE VIEW sonyliv_concurrency.concurrency_now AS
SELECT country, platform, video_type, category, minute, content_id, concurrent
FROM sonyliv_concurrency.concurrency_cold_abs FINAL          -- dedup ReplacingMergeTree
UNION ALL
SELECT country, platform, video_type, category, minute, content_id, concurrent
FROM sonyliv_concurrency.concurrency_hot_abs
WHERE minute > (SELECT max(minute) FROM sonyliv_concurrency.concurrency_cold_abs);

-- =====================================================================
-- D. MATERIALIZED VIEWS (populate the tables)
-- =====================================================================

-- D1. events_incoming → events_raw (cast ms → DateTime64). Incremental.
CREATE MATERIALIZED VIEW IF NOT EXISTS sonyliv_concurrency.mv_incoming_to_raw
TO sonyliv_concurrency.events_raw AS
SELECT video_session_id, user_id, content_id, event_type, event,
       fromUnixTimestamp64Milli(event_timestamp, 'UTC')     AS event_timestamp,
       fromUnixTimestamp64Milli(session_start_epoch, 'UTC') AS session_start_epoch,
       -- column order MUST match events_raw (positional MV insert):
       -- platform, app_version, country, audio_language, subtitle_language, player_version.
       -- normalize the 4 extended dims at the edge (config.sql) so events_raw is
       -- canonical and drill-down filters don't fragment (hin/HIN/hin-hindi):
       platform,
       norm_dim(app_version)        AS app_version,
       country,
       norm_lang(audio_language)    AS audio_language,
       norm_lang(subtitle_language) AS subtitle_language,
       norm_dim(player_version)     AS player_version
FROM sonyliv_concurrency.events_incoming;

-- D2. events_raw → session_intervals (state machine, recent 30-min window).
-- Events collapsed per (session, ms): deactivate > reactivate > neutral (determinism).
DROP VIEW IF EXISTS sonyliv_concurrency.mv_session_intervals;
CREATE MATERIALIZED VIEW sonyliv_concurrency.mv_session_intervals
REFRESH EVERY 1 MINUTE TO sonyliv_concurrency.session_intervals EMPTY AS
SELECT video_session_id, interval_idx, active_start, active_end,
       toUInt8(active_end >= now() - toIntervalSecond(cfg_gap_timeout_seconds())) AS is_provisional,
       content_id, platform, country,
       dictGet('sonyliv_concurrency.content_dict','video_type', content_id) AS video_type,
       dictGet('sonyliv_concurrency.content_dict','category',   content_id) AS category,
       toUnixTimestamp64Milli(now64(3)) AS version
FROM
(
  WITH
  recent AS (
    SELECT video_session_id FROM sonyliv_concurrency.events_raw
    GROUP BY video_session_id HAVING max(event_timestamp) >= now() - INTERVAL 20 MINUTE ),
  per_event AS (
    SELECT video_session_id AS sid, event_timestamp AS ts, content_id, platform, country,
      multiIf(event_type IN ('VideoPlay','AppForegrounded') OR event IN ('resume','speed-resume','AdResume'), 1,
              event_type IN ('AppBackgrounded','VideoSessionEnd','VideoError') OR event IN ('pause','speed-pause','AdPause'), -1,
              0) AS transition
    FROM sonyliv_concurrency.events_raw
    WHERE video_session_id IN (SELECT video_session_id FROM recent) ),
  collapsed AS (
    SELECT sid, ts, if(min(transition) < 0, toInt8(-1), toInt8(max(transition))) AS transition,
           any(content_id) AS content_id, any(platform) AS platform, any(country) AS country
    FROM per_event GROUP BY sid, ts ),
  stated AS (
    SELECT sid, ts, content_id, platform, country,
      argMax(transition, if(transition!=0, ts, toDateTime64('1970-01-01 00:00:00',3,'UTC')))
        OVER (PARTITION BY sid ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS state_sign,
      row_number() OVER (PARTITION BY sid ORDER BY ts) AS rn,
      count()      OVER (PARTITION BY sid)             AS n,
      leadInFrame(ts) OVER (PARTITION BY sid ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS next_ts
    FROM collapsed ),
  segments AS (
    SELECT sid, content_id, platform, country, ts AS seg_start,
      multiIf(rn=n, addSeconds(ts, cfg_heartbeat_seconds()), dateDiff('second', ts, next_ts) <= cfg_gap_timeout_seconds(), next_ts, addSeconds(ts, cfg_heartbeat_seconds())) AS seg_end
    FROM stated WHERE state_sign = 1 ),
  islands AS (
    SELECT *, if(seg_start > max(seg_end) OVER (PARTITION BY sid ORDER BY seg_start
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 1, 0) AS new_island
    FROM segments )
  SELECT sid AS video_session_id, toUInt16(island_id) AS interval_idx,
         min(seg_start) AS active_start, max(seg_end) AS active_end,
         any(content_id) AS content_id, any(platform) AS platform, any(country) AS country
  FROM (SELECT *, sum(new_island) OVER (PARTITION BY sid ORDER BY seg_start
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS island_id FROM islands)
  GROUP BY sid, island_id HAVING active_end > active_start
);

-- D3. session_intervals → concurrency_hot_abs (recent minutes, absolute). 30s REPLACE.
DROP VIEW IF EXISTS sonyliv_concurrency.concurrency_hot_abs_mv;
CREATE MATERIALIZED VIEW sonyliv_concurrency.concurrency_hot_abs_mv
REFRESH EVERY 30 SECOND DEPENDS ON sonyliv_concurrency.mv_session_intervals
TO sonyliv_concurrency.concurrency_hot_abs EMPTY AS
SELECT country, platform, video_type, category, minute, content_id,
       toUInt32(uniqExact(video_session_id)) AS concurrent
FROM (
  SELECT video_session_id, country, platform, video_type, category, content_id,
         -- configurable bucket (config.sql): start-of-bucket + N buckets
         toStartOfInterval(active_start, toIntervalSecond(cfg_bucket_seconds()))
           + toIntervalSecond(number * cfg_bucket_seconds()) AS minute
  FROM sonyliv_concurrency.session_intervals FINAL
  ARRAY JOIN range(0, toUInt64(dateDiff('second',
                 toStartOfInterval(active_start, toIntervalSecond(cfg_bucket_seconds())),
                 toStartOfInterval(active_end - INTERVAL 1 MILLISECOND, toIntervalSecond(cfg_bucket_seconds())))
                 / cfg_bucket_seconds()) + 1) AS number
  WHERE active_end > active_start
)
WHERE minute > toStartOfInterval(now(), toIntervalSecond(cfg_bucket_seconds())) - INTERVAL 10 MINUTE
GROUP BY country, platform, video_type, category, minute, content_id;

-- D4. COLD compaction — schedule this INSERT every ~1 min (app/cron), NOT a view:
--   INSERT INTO sonyliv_concurrency.concurrency_cold_abs
--   SELECT country,platform,video_type,category,minute,content_id, toUInt32(uniqExact(video_session_id))
--   FROM ( <same expand as D3> )
--   WHERE minute <= toStartOfInterval(now(), toIntervalSecond(cfg_bucket_seconds())) - INTERVAL 10 MINUTE
--     AND minute >  (SELECT max(minute) FROM sonyliv_concurrency.concurrency_cold_abs)
--   GROUP BY country,platform,video_type,category,minute,content_id;

SHOW TABLES FROM sonyliv_concurrency;
