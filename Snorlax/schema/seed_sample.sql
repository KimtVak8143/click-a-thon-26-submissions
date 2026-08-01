-- #####################################################################
-- seed_sample.sql — placeholder data to smoke-test the schema without CSVs/Kafka.
-- Seeds the mapping table (content_dim) + dictionary, and a few live sessions
-- (timestamps relative to now()), then refreshes the MVs so concurrency_now
-- has data to query. Run AFTER schema.sql.
--   clickhouse client --host <h> --user default --secure --queries-file seed_sample.sql
-- #####################################################################

-- 1) MAPPING TABLE + DICTIONARY -------------------------------------------------
INSERT INTO sonyliv_concurrency.content_dim (content_id, title, video_type, category) VALUES
  (100, 'Live Match A', 'live', 'sports'),
  (101, 'Movie B',      'vod',  'drama'),
  (102, 'Show C',       'vod',  'comedy');

SYSTEM RELOAD DICTIONARY sonyliv_concurrency.content_dict;

-- verify the mapping/dictionary resolves
SELECT 100 AS content_id,
       dictGet('sonyliv_concurrency.content_dict','title',      toUInt64(100)) AS title,
       dictGet('sonyliv_concurrency.content_dict','video_type', toUInt64(100)) AS video_type,
       dictGet('sonyliv_concurrency.content_dict','category',   toUInt64(100)) AS category;

-- 2) SAMPLE EVENTS (last ~8 min, so the live MVs pick them up) ------------------
-- Session A: content 100, plays throughout, still open (no end)
-- Session B: content 100, pauses min-4→min-3 (excluded), then ends
-- Session C: content 101, plays then ends
INSERT INTO sonyliv_concurrency.events_raw
  (video_session_id, user_id, content_id, event_type, event, event_timestamp, session_start_epoch,
   platform, app_version, country, audio_language, subtitle_language, player_version)
VALUES
  ('sA','uA',100,'VideoSessionStart','VideoSessionStart', now64(3)-INTERVAL 8 MINUTE, now64(3)-INTERVAL 8 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sA','uA',100,'VideoPlay','Play',                       now64(3)-INTERVAL 8 MINUTE, now64(3)-INTERVAL 8 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sA','uA',100,'VideoHeartbeat','buffer-health',         now64(3)-INTERVAL 7 MINUTE, now64(3)-INTERVAL 8 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sA','uA',100,'VideoHeartbeat','buffer-health',         now64(3)-INTERVAL 5 MINUTE, now64(3)-INTERVAL 8 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sA','uA',100,'VideoHeartbeat','buffer-health',         now64(3)-INTERVAL 3 MINUTE, now64(3)-INTERVAL 8 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sA','uA',100,'VideoHeartbeat','buffer-health',         now64(3)-INTERVAL 1 MINUTE, now64(3)-INTERVAL 8 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),

  ('sB','uB',100,'VideoSessionStart','VideoSessionStart', now64(3)-INTERVAL 6 MINUTE, now64(3)-INTERVAL 6 MINUTE,'IPHONE','1.0','india','eng','unk','1.0'),
  ('sB','uB',100,'VideoPlay','Play',                       now64(3)-INTERVAL 6 MINUTE, now64(3)-INTERVAL 6 MINUTE,'IPHONE','1.0','india','eng','unk','1.0'),
  ('sB','uB',100,'VideoHeartbeat','buffer-health',         now64(3)-INTERVAL 5 MINUTE, now64(3)-INTERVAL 6 MINUTE,'IPHONE','1.0','india','eng','unk','1.0'),
  ('sB','uB',100,'VideoHeartbeat','pause',                 now64(3)-INTERVAL 4 MINUTE, now64(3)-INTERVAL 6 MINUTE,'IPHONE','1.0','india','eng','unk','1.0'),
  ('sB','uB',100,'VideoHeartbeat','resume',                now64(3)-INTERVAL 3 MINUTE, now64(3)-INTERVAL 6 MINUTE,'IPHONE','1.0','india','eng','unk','1.0'),
  ('sB','uB',100,'VideoSessionEnd','VideoSessionEnd',      now64(3)-INTERVAL 1 MINUTE, now64(3)-INTERVAL 6 MINUTE,'IPHONE','1.0','india','eng','unk','1.0'),

  ('sC','uC',101,'VideoSessionStart','VideoSessionStart', now64(3)-INTERVAL 5 MINUTE, now64(3)-INTERVAL 5 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sC','uC',101,'VideoPlay','Play',                       now64(3)-INTERVAL 5 MINUTE, now64(3)-INTERVAL 5 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sC','uC',101,'VideoHeartbeat','buffer-health',         now64(3)-INTERVAL 4 MINUTE, now64(3)-INTERVAL 5 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0'),
  ('sC','uC',101,'VideoSessionEnd','VideoSessionEnd',      now64(3)-INTERVAL 2 MINUTE, now64(3)-INTERVAL 5 MINUTE,'ANDROID_PHONE','1.0','india','hin','unk','1.0');

-- 3) populate the serving layer from the seed
SYSTEM REFRESH VIEW sonyliv_concurrency.mv_session_intervals;
SYSTEM WAIT VIEW    sonyliv_concurrency.mv_session_intervals;
SYSTEM REFRESH VIEW sonyliv_concurrency.concurrency_hot_abs_mv;
SYSTEM WAIT VIEW    sonyliv_concurrency.concurrency_hot_abs_mv;

-- 4) smoke test — should show a concurrency curve for the last few minutes
SELECT minute, sum(concurrent) AS concurrency
FROM sonyliv_concurrency.concurrency_now
GROUP BY minute ORDER BY minute;
