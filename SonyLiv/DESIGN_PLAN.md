# SonyLIV — Foreground-Only Concurrency: Design Plan

> Counting the crowd at streaming scale. Count only **truly active** playback
> (exclude paused / backgrounded / heartbeat-missing periods), answer minute-grain
> filtered dashboard queries instantly, and stay update-friendly as live sessions
> keep changing.

---

## 1. Problem, restated

- **Input:** ~905K raw streaming events (`ch-hackathon-raw-data.csv`) + ~33K content
  titles (`ch-hackathon-content-data.csv`). Event stream per session:
  `VideoSessionStart, VideoPlay, VideoHeartbeat, AppBackgrounded, AppForegrounded,
  VideoSessionEnd, VideoError`.
- **Heartbeat** fires every 1 minute while active. `AppBackgrounded` /
  `AppForegrounded` are **not guaranteed** — the system may drop them.
- **Filter dimensions:** platform, app_version, country, audio_language,
  subtitle_language, player_version, + content `title / video_type / category`.
  The design must survive **new dimensions being added**.
- **Deliverables graded on:** correctness vs. private ground truth, query
  performance, update handling, design quality, and results on an **unseen day**.

### Why the raw table can't serve this
- Per-minute concurrency from raw events = a range-overlap scan over 905K+ rows
  per query. Doesn't scale, and re-scans on every filter combo.
- Peak/avg over a window are **non-additive across dimensions** — you cannot
  precompute per-dimension peaks and add them. The peak minute differs per filter.
- Sessions are **open/mutable**: late heartbeats and missing end events mean any
  precomputed absolute table would need constant rewriting.

---

## 2. Core model: interval → delta → cumulative sum

Three insights drive the whole design:

1. **Active interval `[start, end)`** (half-open, minute granularity). Each active
   interval contributes `+1` at `start_minute` and `−1` at `end_minute`.
   Concurrency at any minute = running cumulative sum of deltas up to that minute.
2. **Deltas are additive across dimensions.** Filter to any dimension combo, sum
   the deltas, re-run the cumsum → concurrency for that slice. New dimensions =
   just more columns in the sort key; no model change.
3. **Instantaneous counts are additive** (only peak/avg *over a range* are not).
   So a frozen per-minute per-dimension **absolute** table can be filtered by
   summing — the basis for the cold tier.

### The 4 stages
| Stage | What | ClickHouse object |
|---|---|---|
| 1 | Raw events | `MergeTree` (append-only ingest) |
| 2 | Active intervals `[start,end)` (foreground logic) | `MV` off raw |
| 3 | Minute deltas (`+1`/`−1`, collapsed by dims+minute) | `SummingMergeTree` |
| 4 | Query: filter → sum → cumsum → max/avg | serving `VIEW` / query |

---

## 3. Stage 2 — deriving *foreground-only* active intervals

This is where correctness is won. A session is **active in minute M** iff it has a
heartbeat in M **and** is not backgrounded.

- Consecutive active minutes → one interval `[first, last+1)`.
- **Gap rule:** a missing-heartbeat gap larger than `GRACE` (heartbeat interval +
  timeout, ~90s) **splits** the interval — this catches backgrounding even when
  `AppBackgrounded` never fired (the not-guaranteed case).
- **Background rule:** an explicit `AppBackgrounded` hard-cuts the current
  interval; activity resumes on the next heartbeat / `AppForegrounded`.
- **Open sessions:** no `VideoSessionEnd` → interval is provisional
  `[start, last_heartbeat+1)`, flagged `is_open`. No `−1` emitted yet.

> Demo: `demo_pipeline.py` (clean, backgrounded, heartbeat-gap-no-event, open).

---

## 4. Windowed queries — the carry-in problem

Concurrency is a running total over **all** history, so a query window can contain
active viewers even if **no delta rows fall inside it** (long intervals straddling
the window).

- **Wrong:** cumsum over window-local deltas only → misses everyone who started
  before the window.
- **Right:** `carry_in` = sum of all deltas **before** the window start, then apply
  in-window deltas, **gap-filling** empty minutes with the carried running total.

> Demo: `demo_gap.py` (intervals with nothing at minutes 3–5).

---

## 5. Hot / Cold tiering

Split at a moving **watermark**: minutes older than it are *final*; newer minutes
are *mutable*.

| Tier | Represents | Stored as | Why |
|---|---|---|---|
| **COLD** | frozen history (`minute < watermark`) | **absolute** concurrency per (dims, minute) | read-optimized: direct lookup, filter-additive, append-only |
| **HOT** | recent + open sessions (`minute ≥ watermark`) | **deltas** (from intervals) | update-friendly: late heartbeats / ends just append rows |

### What lands in the HOT delta table
| Session shape | `+1` in hot | `−1` in hot | Reason |
|---|---|---|---|
| started cold, **ends** in hot | no | **yes** | its `+1` is already baked into cold |
| starts in hot, still open | **yes** | no | brand-new arrival |
| started cold, still **open** | no | no | fully covered by the carry-in |
| fully cold | no | no | entirely in cold |

### The stitch (spanning query)
For a window crossing the boundary:
- **Cold minutes:** direct read from the absolute table.
- **carry-in for hot** = cold's absolute value at the boundary minute (one cell —
  already includes every session alive at the crossover).
- **Hot minutes:** `carry_in + cumsum(hot deltas)`.

> Demo: `demo_hotcold.py` (Android/IN mins 4–8 → `2 2 2 3 2`, peak 3, avg 2.2;
> update P-ends-at-11 handled by one appended delta, no rebuild).

### Compaction (hot → cold graduation)
As the watermark advances, for each newly-final minute compute its absolute
concurrency **once** (`carry_in + hot deltas up to it`), write to cold, drop those
hot deltas. Hot stays bounded; cold grows append-only. This is the only heavy op,
and it's incremental.

### Snapshots / checkpoints
For long-running live events, periodic absolute snapshots inside cold bound the
carry-in scan to "since last checkpoint" instead of "all history."

---

## 6. The watermark trade-off (the one real knob)

All tuning collapses to **how much recent, mutable state stays hot**:

1. **Late data vs. read speed.** Wide hot → late heartbeats never hit frozen cold
   (no rewrites), but bigger cumsum per query. Narrow hot → fast reads, but risk
   freezing a minute before stragglers arrive. Size hot to observed heartbeat-lag
   (e.g. p99 lag 4 min → keep ≥5 min hot).
2. **Compaction frequency vs. hot size.** Freeze rarely → hot bloats; freeze often
   → repeated cost + higher late-data risk.
3. **Snapshot spacing vs. carry-in cost.** Frequent snapshots → cheap carry-in,
   more storage; sparse → cheaper storage, longer carry-in scans.

Net: **more hot = correctness under late/out-of-order data; more cold = query
speed + cheap storage.** These thresholds must be set from *measured* lag and
latency — see §8.

---

## 7. ClickHouse objects — MV vs. VIEW

A **Materialized View is an insert trigger on one source table**, not a live
spanning view. It cannot stitch hot+cold or maintain carry-in-cumsum on read. Use:

| Object | Job | Fires when |
|---|---|---|
| **MV #1** | raw events → intervals → `hot_delta` | on INSERT (incremental) |
| **MV #2 / scheduled job** | freeze finalized minutes → `cold_abs` | at compaction |
| **normal `VIEW`** | stitch hot + cold at read time | on every query |

### Serving VIEW (stitch), sketch
```sql
CREATE VIEW concurrency_now AS
WITH (SELECT max(minute) FROM cold_abs) AS watermark
SELECT minute, sum(active) AS concurrent
FROM (
    -- COLD: absolute, minute < watermark
    SELECT minute, active FROM cold_abs WHERE {filters}
    UNION ALL
    -- HOT: carry-in seed + cumsum of hot deltas
    SELECT minute,
        (SELECT sum(active) FROM cold_abs WHERE minute = watermark AND {filters})
        + sum(sum(delta)) OVER (ORDER BY minute) AS active
    FROM hot_delta WHERE minute > watermark AND {filters}
    GROUP BY minute
)
GROUP BY minute
ORDER BY minute WITH FILL;   -- gap-fill empty minutes with the running total
```

Peak/avg are computed on top of this per-minute curve (`max()`, `avg()`), never
precombined across dimensions.

---

## 8. Observability integration (required)

**ClickStack** is the natural fit — it turns §6's guesswork into measurement:
- heartbeat-lag distribution (p50/p99) → sets the watermark width.
- query latency vs. hot-window size → validates read SLAs.
- compaction cost / frequency → tunes the freeze cadence.
- On the **unseen day**, the same dashboards confirm whether the chosen watermark
  still holds under that day's lag profile.

---

## 9. Implementation plan (build order)

1. **DDL:** `raw_events` (MergeTree), `hot_delta` (SummingMergeTree, ORDER BY
   dims…, minute), `cold_abs` (SummingMergeTree, ORDER BY dims…, minute).
2. **MV #1** raw → active intervals → hot_delta (encode Stage-2 foreground rules:
   gap split, background cut, open-session handling).
3. **Cold-freeze job** (scheduled `INSERT … SELECT` across the watermark +
   `ALTER TABLE hot_delta DROP PARTITION`).
4. **Serving VIEW** (§7 stitch) + parameterized filter/window wrapper.
5. **Content join** for `title / video_type / category` dimensions.
6. **Load** the synthetic CSVs, validate curves against a brute-force
   range-overlap query on the raw table (ground-truth check).
7. **ClickStack** wiring + watermark tuning from measured lag.
8. **Unseen-day** run: pipe the sealed dataset through, capture output + evidence.

## 10. Open decisions
- Exact `GRACE` / timeout value for the gap-split (needs the real lag distribution).
- Watermark width and compaction cadence (set from §8 measurements).
- Snapshot interval for long live events.
- Whether user-level concurrency (`user_id` dedup across sessions) is required in
  addition to session-level — dedup breaks simple delta additivity and may need a
  separate path.

## Appendix — demo scripts
- `demo_pipeline.py` — Stage 1→2→3→4 on 4 synthetic sessions.
- `demo_gap.py` — carry-in / windowed-query correctness.
- `demo_hotcold.py` — hot/cold tiering, spanning query, update-without-rebuild.
