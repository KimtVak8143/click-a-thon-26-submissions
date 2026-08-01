-- #####################################################################
-- 05_verification.sql — correctness checks.
-- #####################################################################

-- ---------------------------------------------------------------------
-- A) SERVING == BRUTE FORCE. The tiered serving (cold+hot) must equal an
--    independent per-minute explosion from session_intervals (distinct
--    sessions per minute). Expect ZERO mismatched rows.
--    NOTE: this only checks the expand+aggregate step — session_intervals is
--    both the reference's and serving's shared input, so it CANNOT catch a
--    bug in the state machine itself. See check A2 below for that.
-- ---------------------------------------------------------------------
WITH
serving AS (
  SELECT minute, sum(concurrent) AS c
  FROM sonyliv_concurrency.concurrency_now GROUP BY minute
),
reference AS (
  SELECT minute, uniqExact(video_session_id) AS c
  FROM (
    SELECT video_session_id,
           -- configurable bucket (config.sql): start-of-bucket + N buckets
           toStartOfInterval(active_start, toIntervalSecond(cfg_bucket_seconds()))
             + toIntervalSecond(number * cfg_bucket_seconds()) AS minute
    FROM (
      -- unpack session_intervals' one-row-per-session Array(Tuple(...)) first
      -- (ghost-interval fix, review #7).
      SELECT video_session_id, iv.1 AS active_start, iv.2 AS active_end
      FROM sonyliv_concurrency.session_intervals FINAL
      ARRAY JOIN intervals AS iv
      WHERE iv.2 > iv.1
    )
    ARRAY JOIN range(0, toUInt64(dateDiff('second',
                   toStartOfInterval(active_start, toIntervalSecond(cfg_bucket_seconds())),
                   toStartOfInterval(active_end - INTERVAL 1 MILLISECOND, toIntervalSecond(cfg_bucket_seconds())))
                   / cfg_bucket_seconds()) + 1) AS number
  )
  GROUP BY minute
)
SELECT s.minute, s.c AS serving_c, r.c AS reference_c
FROM serving s FULL JOIN reference r USING (minute)
WHERE s.c != r.c
ORDER BY minute;                 -- <-- zero rows = correct

-- ---------------------------------------------------------------------
-- A2) INDEPENDENT ORACLE — re-derives active intervals straight from
--     events_raw using a structurally different technique from
--     schema.sql/backfill_history.sql's window-function pipeline (argMax()
--     OVER, row_number() OVER, leadInFrame() OVER, sum() OVER), so a bug in
--     one implementation is very unlikely to reproduce identically in the
--     other. This is the highest-leverage check in this file — check A above
--     can only validate the expand+aggregate step, not the state machine.
--
--     Technique: per-session sorted arrays + arrayFill() to forward-fill the
--     watching state (stand-in for argMax() OVER) + direct array-index
--     lookahead ts_arr[i+1] (stand-in for leadInFrame() OVER). Segments are
--     NOT merged into islands (unlike the production pipeline) — for a
--     per-minute-membership oracle, "does ANY segment cover minute m" is
--     mathematically equivalent to "does the merged island set cover minute
--     m", so skipping the merge step removes an entire class of shared bugs
--     the two implementations could otherwise accidentally share.
--
--     Reads the SAME config.sql knobs (cfg_heartbeat_seconds, cfg_gap_timeout_
--     seconds, cfg_bucket_seconds) and the same deactivate>reactivate>neutral
--     collapse priority as the production state machine, so tuning a knob
--     doesn't make this oracle go stale and report false mismatches. Those
--     knob VALUES are themselves under test elsewhere (see the
--     VideoSessionStart / tuning_variants checks below), not by this oracle.
-- ---------------------------------------------------------------------
WITH
transitions AS (
  SELECT video_session_id AS sid, event_timestamp AS ts,
         multiIf(event_type IN ('VideoPlay','AppForegrounded') OR event IN ('resume','speed-resume','AdResume'), 1,
                 event_type IN ('AppBackgrounded','VideoSessionEnd','VideoError') OR event IN ('pause','speed-pause','AdPause'), -1,
                 0) AS transition
  FROM sonyliv_concurrency.events_raw
),
collapsed AS (
  SELECT sid, ts, if(min(transition) < 0, -1, max(transition)) AS transition
  FROM transitions GROUP BY sid, ts
),
per_session AS (
  SELECT sid,
         arrayMap(p -> p.1, arraySort(x -> x.1, groupArray((ts, transition)))) AS ts_arr,
         arrayMap(p -> p.2, arraySort(x -> x.1, groupArray((ts, transition)))) AS tr_arr
  FROM collapsed
  GROUP BY sid
),
stated AS (
  SELECT sid, ts_arr, arrayFill(x -> x != 0, tr_arr) AS state_arr, length(ts_arr) AS n
  FROM per_session
),
exploded AS (
  SELECT sid, ts_arr, state_arr, n, arrayJoin(arrayEnumerate(ts_arr)) AS i
  FROM stated
),
active_events AS (
  SELECT sid, ts_arr[i] AS seg_start,
         if(i = n, addSeconds(ts_arr[i], cfg_heartbeat_seconds()),
            if(dateDiff('second', ts_arr[i], ts_arr[i+1]) <= cfg_gap_timeout_seconds(), ts_arr[i+1],
               addSeconds(ts_arr[i], cfg_heartbeat_seconds()))) AS seg_end
  FROM exploded
  WHERE state_arr[i] = 1
),
oracle_session_minutes AS (
  SELECT DISTINCT sid AS video_session_id,
         toStartOfInterval(seg_start, toIntervalSecond(cfg_bucket_seconds()))
           + toIntervalSecond(number * cfg_bucket_seconds()) AS minute
  FROM active_events
  ARRAY JOIN range(0, toUInt64(dateDiff('second',
                 toStartOfInterval(seg_start, toIntervalSecond(cfg_bucket_seconds())),
                 toStartOfInterval(seg_end - INTERVAL 1 MILLISECOND, toIntervalSecond(cfg_bucket_seconds())))
                 / cfg_bucket_seconds()) + 1) AS number
  WHERE seg_end > seg_start
),
reference_session_minutes AS (
  SELECT DISTINCT video_session_id,
         toStartOfInterval(active_start, toIntervalSecond(cfg_bucket_seconds()))
           + toIntervalSecond(number * cfg_bucket_seconds()) AS minute
  FROM (
    SELECT video_session_id, iv.1 AS active_start, iv.2 AS active_end
    FROM sonyliv_concurrency.session_intervals FINAL
    ARRAY JOIN intervals AS iv
    WHERE iv.2 > iv.1
  )
  ARRAY JOIN range(0, toUInt64(dateDiff('second',
                 toStartOfInterval(active_start, toIntervalSecond(cfg_bucket_seconds())),
                 toStartOfInterval(active_end - INTERVAL 1 MILLISECOND, toIntervalSecond(cfg_bucket_seconds())))
                 / cfg_bucket_seconds()) + 1) AS number
)
SELECT
  (SELECT count() FROM oracle_session_minutes AS o
     LEFT ANTI JOIN reference_session_minutes AS r
     ON o.video_session_id = r.video_session_id AND o.minute = r.minute) AS oracle_only_count,
  (SELECT count() FROM reference_session_minutes AS r
     LEFT ANTI JOIN oracle_session_minutes AS o
     ON r.video_session_id = o.video_session_id AND r.minute = o.minute) AS reference_only_count;
  -- both 0 = correct (Nirad's bar: N/N identical, interval-by-interval)

-- ---------------------------------------------------------------------
-- B) PER-SESSION DEDUPE probe: a session's own array can still list >1
--    interval landing in the same minute; uniqExact upstream handles the
--    once-per-minute dedupe regardless. List any for a sanity spot check.
-- ---------------------------------------------------------------------
SELECT video_session_id, toStartOfInterval(active_start, toIntervalSecond(cfg_bucket_seconds())) AS minute, count() AS intervals_in_minute
FROM (
  SELECT video_session_id, iv.1 AS active_start
  FROM sonyliv_concurrency.session_intervals FINAL
  ARRAY JOIN intervals AS iv
)
GROUP BY video_session_id, minute
HAVING intervals_in_minute > 1
ORDER BY intervals_in_minute DESC LIMIT 20;

-- ---------------------------------------------------------------------
-- E) VideoSessionStart lead-in check: VideoSessionStart is currently neutral
--    (transition=0) in the state machine, so any time between a session's
--    VideoSessionStart and its first VideoPlay/resume is dropped as inactive.
--    If a meaningful fraction of sessions heartbeat/lead-in before their
--    first VideoPlay, VideoSessionStart likely belongs in the +1 branch
--    instead (verify against the real dataset before changing production).
-- ---------------------------------------------------------------------
SELECT
  count() AS sessions_with_start_and_play,
  countIf(gap_seconds > 0) AS sessions_with_lead_in,
  round(100.0 * countIf(gap_seconds > 0) / count(), 1) AS pct_with_lead_in,
  round(avgIf(gap_seconds, gap_seconds > 0), 1) AS avg_lead_in_seconds
FROM (
  SELECT video_session_id,
         dateDiff('second',
           minIf(event_timestamp, event_type = 'VideoSessionStart'),
           minIf(event_timestamp, event_type = 'VideoPlay' OR event IN ('resume','speed-resume','AdResume'))
         ) AS gap_seconds
  FROM sonyliv_concurrency.events_raw
  GROUP BY video_session_id
  HAVING countIf(event_type = 'VideoSessionStart') > 0
     AND countIf(event_type = 'VideoPlay' OR event IN ('resume','speed-resume','AdResume')) > 0
);

-- ---------------------------------------------------------------------
-- C) PAUSE-CORRECTNESS: our foreground-only count vs the NAIVE
--    "heartbeat-present in minute" rule (PLAN2). Naive counts paused
--    time as active -> it should be consistently HIGHER. The gap is the
--    overcount we avoid. (Total + a few worst minutes.)
-- ---------------------------------------------------------------------
WITH
ours AS (
  SELECT minute, sum(concurrent) AS c FROM sonyliv_concurrency.concurrency_now GROUP BY minute
),
naive AS (   -- "active iff any heartbeat in the minute" (ignores pause)
  SELECT toStartOfInterval(event_timestamp, toIntervalSecond(cfg_bucket_seconds())) AS minute,
         uniqExact(video_session_id) AS c
  FROM sonyliv_concurrency.events_raw
  WHERE event_type = 'VideoHeartbeat'
  GROUP BY minute
)
SELECT
  sum(n.c) AS naive_total_session_minutes,
  sum(o.c) AS ours_total_session_minutes,
  sum(n.c) - sum(o.c) AS overcount_avoided,
  round(100.0*(sum(n.c)-sum(o.c))/sum(n.c), 1) AS pct_overcount_avoided
FROM naive n LEFT JOIN ours o USING (minute);

-- ---------------------------------------------------------------------
-- D) Cold/Hot split sanity: tiers must be disjoint by minute.
-- ---------------------------------------------------------------------
SELECT
  (SELECT max(minute) FROM sonyliv_concurrency.concurrency_cold_abs) AS cold_max_minute,
  (SELECT min(minute) FROM sonyliv_concurrency.concurrency_hot_abs)  AS hot_min_minute,
  (SELECT count() FROM (
      SELECT minute FROM sonyliv_concurrency.concurrency_cold_abs
      INTERSECT
      SELECT minute FROM sonyliv_concurrency.concurrency_hot_abs)) AS overlapping_minutes;  -- expect 0
