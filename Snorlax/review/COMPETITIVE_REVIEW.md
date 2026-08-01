# Snorlax — Competitive Review & Fix List

*Comparison of our `plan/PLAN.md` + `schema/*.sql` against the three other SonyLIV
concurrency submissions (`sonyliv-clickathon-2026`, `nirad-sonyliv-concurrency`,
`phoenix-concurrency`), plus a line-level audit of our own SQL.*

Ordered by impact on the actual judging axes: correctness vs private key → what
queries READ → update handling → design → mandatory integration → demo.

---

## Where we actually stand

| Axis | Snorlax vs field | Verdict |
|---|---|---|
| **Read cost (narrow-range queries)** | Our **absolute per-(dims,minute)** store beats Nirad's delta+cumsum (their checkpoint saved 0.4%, reads ~100K rows/query) and Phoenix's cumsum-from-series-start (reads whole history). | **We win — if we prove it with `query_log`.** |
| **Hot-tier freshness** | Our materialized 30s REPLACE hot tier is what Nirad lists as their #1 unbuilt future work, and sonyliv killed their live tier entirely. | **We win.** |
| **Real streaming ingest** | Redpanda→ClickPipes vs everyone else's batch CSV `INSERT`. | **We win — but it's unbuilt/unrun.** |
| **Correctness *evidence*** | Nirad has an independent oracle + 6 sealed bundles + `query_log.tsv`; sonyliv has hash-attested rebuild gates. **Ours (`verify.sql`) is not an independent oracle.** | **We lose badly right now.** |
| **Mandatory integration** | Nirad has a real ClickStack OTLP exporter with trace IDs. Phoenix & sonyliv have *zero*. | **Field is split; we can win.** |
| **Exact-metric semantics** | sonyliv computes exact time-weighted peak/avg; we count overlap. Coin-flip vs private key. | **At risk — must hedge.** |

**Headline:** our serving architecture is the best of the four, but our
correctness evidence is the worst — and evidence is the axis the problem statement
weights hardest ("no pipeline evidence, no credit"). That's the gap to close.

---

## P0 — Correctness bugs in our own SQL (fix before any judged run)

**1. `content_id UInt64` will corrupt the load.**
The content catalog contains a negative sentinel `-987654322` (both sonyliv and
Nirad hit this). We use `UInt64` in `events_incoming`, `events_raw`, `content_dim`,
`session_intervals`, `concurrency_*` — every one. Negative IDs either abort the CSV
load or wrap to a garbage huge int, silently breaking joins/filters on that content.
**Fix: `content_id Int64` everywhere** (schema.sql:48,60,76,85,96,101).

**2. Our `verify.sql` is not an independent oracle — it validates almost nothing.**
`verify.sql:15-27` builds its "reference" by exploding `session_intervals` — the
*output of the state machine* — and compares to serving, which is also built from
`session_intervals`. It only checks the expand+aggregate step and **cannot catch a
state-machine bug**, which is exactly where correctness is won or lost. Nirad
re-derives intervals from **raw events** in independent Python and diffs
interval-by-interval (35,901/35,901 identical); Phoenix re-implements the state
machine in SQL and gates to 0 diffs.
**Fix: write an independent brute-force oracle from `events_raw` (a second,
differently-written derivation) and diff against serving.** Highest-leverage change
we can make.

**3. Hour/day average is biased high (same bug Phoenix shipped).**
`ui_queries.sql:132-134` computes `round(avg(c),1)` over only the minutes present in
`curve` — zero-activity minutes are absent, so quiet minutes are skipped and the
average over-reports. Our full-range KPI (query 2, line 50) does it right
(`sum(c)/(dateDiff+1)`), but the hour/day query doesn't.
**Fix: densify with `WITH FILL STEP toIntervalMinute(1)` before averaging, or divide
by the true minute count in each bucket.**

**4. `AppForegrounded` reactivation + 60s grace are two un-hedged coin-flips.**
- sonyliv found **13,382/14,256 foregrounds leave playback stopped** — they treat
  `AppForegrounded` as *not* resuming. We reactivate on it (schema.sql:157,
  backfill:31). If the key agrees with sonyliv, we over-count.
- Our **60s grace** (seg_end = `ts+60s`, backfill:53-55) is more generous than both
  Nirad (grace=0) and Phoenix (grace=0), who argue overcount is the failure mode
  judges punish.
- Our **90s gap** is *below* Nirad's measured p99 heartbeat gap of 96.4s — we'd
  prematurely split ~1% of legitimate sessions.

**Fix: parameterize these three knobs** (foreground-resumes y/n, grace 0/60,
gap 90/120) and compute all variants rather than hardcoding. sonyliv's
`policy.yaml`-as-contract is the model. Pin to the benchmark's provided answers
before the unseen day.

**5. `VideoSessionStart` is neutral in our machine.**
In `per_event` (schema.sql:157, backfill:31) only `VideoPlay`/`AppForegrounded`/
resume flip to +1 — `VideoSessionStart` → transition 0. Phoenix and Nirad both open
on `VideoSessionStart`. If any session heartbeats before its first `VideoPlay`, we
drop that lead-in as inactive. **Verify against the data; likely should be a +1 start.**

**6. Cloud dictionary trap — and backfill's comment contradicts its code.**
`dictGet('content_dict',...)` is used in the hot MV (schema.sql:146-147) and backfill
(backfill:23-24), but on ClickHouse Cloud `SYSTEM RELOAD DICTIONARY` is **node-local**
— Nirad's `video_type='live'` silently returned **0 instead of 469** on Cloud from a
stale replica dictionary. Worse, `backfill_history.sql:8-9` *comments* "content
enriched via JOIN (no dictionary)" but the code uses `dictGet`.
**Fix: switch enrichment to a `LEFT JOIN content_dim` (LEFT so missing content still
counts), or reload `ON CLUSTER`.**

---

## P1 — Serving / architecture bugs

**7. Ghost-interval risk in the live `session_intervals` (Nirad hit this exactly).**
`mv_session_intervals` writes `ReplacingMergeTree(version)` keyed
`(video_session_id, interval_idx)`. If a re-derivation ever produces *fewer* islands
for a session than a previous run, the old high-`interval_idx` rows are never
overwritten and linger — and the hot MV reads `session_intervals FINAL`
(schema.sql:200), so ghosts get counted. Nirad fixed it with a scoped `DELETE` before
re-derive. **Fix: scoped-delete touched sessions before insert, or key the Replacing
table so stale intervals can't survive.**

**8. Live path has a 10-minute memory unless D4 is manually scheduled.**
Cold compaction (schema.sql:209-215) is **commented out** — not automated anywhere in
`schema.sql`. In pure live mode nothing populates `cold_abs`, so `concurrency_now`
serves only the hot tier's last 10 minutes. **Fix: ship D4 as a real scheduled job
(refreshable MV or documented cron in the runbook), and make the view defensive:**
`WHERE minute > coalesce((SELECT max(minute) FROM cold_abs), toDateTime(0))`
(schema.sql:123).

**9. The derivation isn't actually "bounded work."**
The `recent` CTE (schema.sql:152-154) does
`GROUP BY video_session_id HAVING max(event_timestamp) >= now()-20min` — with
`ORDER BY (session, ts)` and monthly partitions, this **full-scans the partition every
minute** to find recent sessions; nothing prunes a 20-min window. The plan's
"recompute ∝ window × active sessions, independent of history" is false as written.
**Fix: add a time-based skip index / minmax on `event_timestamp`, or a small
`last_seen` state table, so recency lookup is O(active) not O(history).**

**10. Unfiltered totals slightly over-count via cross-combo double-counting.**
Summing `concurrent` across dim-combos (ui_queries.sql:26) counts a multi-platform
session once per platform (~95 sessions, 0.9%). Fine for filtered queries, wrong for
the global total vs a global `uniqExact` oracle. **Fix: note it explicitly, and for
the unfiltered headline either accept the documented ~0.9% or serve a separate
all-dims-collapsed row.**

**11. `next_ts - ts <= 90` unit risk.**
DateTime64(3) subtraction in ClickHouse yields a Decimal whose unit isn't obviously
seconds (backfill:54, schema.sql:176). This is one of the "expect engine fixes" the
plan admits — **use `dateDiff('second', ts, next_ts) <= 90` explicitly.**

---

## Queries

- **KPI/breakdown queries filter inconsistently.** Query 2 (ui_queries.sql:46) and
  queries 3–5 filter on **platform only** (or nothing), while the curve (query 1)
  filters all five dims. KPI tiles won't match the filtered chart. **Fix: apply the
  full identical predicate block to every query** (factor it into one snippet).
- **Filter dropdowns hammer the serving view.** `SELECT DISTINCT platform FROM
  concurrency_now` (ui_queries.sql:13-16) forces cold `FINAL` + hot union just to
  populate a dropdown. **Fix: read distincts from a tiny dedicated dimension table or
  `events_raw` with `LIMIT BY`.**
- **Adopt Nirad's `WITH FILL FROM <literal>` discipline** — the fill start must be the
  global first minute, not the filtered slice's first row, or averages drift.

---

## Optimizations

- **Don't switch to deltas.** Confirmed: on this data Nirad's checkpoint gave
  **1.004×** (≈0 benefit, because 94% of events are one day) and Phoenix's cumsum
  reads the entire history. Our absolute store reads only the queried window. **Keep
  it — this is our edge — and *prove* it:** capture `read_rows`/`read_bytes` from
  `system.query_log` for each benchmark query (ui_queries.sql:139 already does this —
  make it a first-class evidence artifact).
- **Add a dimension-first PROJECTION** on `cold_abs`/`hot_abs` (like Nirad's
  `by_dimension`) so filtered-but-wide-range queries prune on dims too, not just the
  minute prefix.
- **Keep high-cardinality dims out of the core key** (plan Fix #7 is right; the SQL
  already does — verify no drift).
- **Set a TTL/partition on `session_intervals`** — it currently grows unbounded in the
  live path (no TTL on schema.sql:88).

---

## Deployment / ingestion

- Our Redpanda→ClickPipes path is the only real streaming story in the field — **but
  it's entirely unbuilt and unrun.** Nirad's whole win is that theirs *ran* and is
  *evidenced*. **Priority: get the pipeline executing end-to-end on Cloud and rehearse
  the unseen-day drill**, or we forfeit our differentiator to a team that shipped batch
  but proved it.
- **Adopt Nirad's one-command sealed-run harness with NO unseen-day special case**:
  input SHA-256 + git commit (run from a **clean** tree — Nirad's blemish was
  `git_dirty:true` on all 6 runs) + per-stage row counts/timings + CH version +
  `query_log.tsv`. This *is* "no pipeline evidence, no credit," executed.
- **Build the open-session fixture correctly.** Nirad's "incremental" demo was nearly
  vacuous — setting watermark = `max(event_ts)` made almost every truncated session
  already-stale (`open_intervals: 1`). **Set "now" so many sessions are genuinely
  active at the cut**, and add Phoenix's **bystander-isolation** assertion (prove the
  incremental refresh touched only the changed sessions, not 200 bystanders).
- **Pin `--session_timezone UTC`** on every client invocation (Phoenix caught a 5:30
  IST drift). Our data is UTC-typed but ad-hoc queries can still drift.

---

## UI / dashboard

We're ahead here by default — Phoenix and Nirad have minimal vanilla-JS SVG
dashboards, sonyliv has **none**. To convert that into points:
- **Make the hero visual the two-curve gap** (naive overlap vs foreground-only) with
  the overcount % — Nirad's most compelling asset (17.4% overall, 26.6% live-on-
  Android). It dramatizes the problem's whole premise.
- **Put a live "read_rows / latency" badge** on the dashboard (we already query it) —
  judges grade "what it reads," so surface it.
- **Downsample by keeping MAX per bucket** so the true peak survives zoom-out (Nirad's
  trick), and compute peak at minute grain *before* downsampling.
- Recompute the naive baseline **live from `events_raw`** so it can't be dismissed as a
  stale strawman.

---

## Integration (mandatory — don't leave points on the table)

Phoenix and sonyliv scored **zero** here (both admit it). Nirad shipped a real stdlib
OTLP exporter with trace IDs. **We must land ClickStack *meaningfully*** — instrument
ingestion lag + per-query `read_rows`/`read_bytes` + pipeline stage timings, and
capture a trace ID as evidence (Nirad's model). If we also do LibreChat+MCP, **produce
an actual transcript** — Nirad's MCP was well-designed but had zero run evidence, so it
counted for little.

---

## The prioritized action list

**Must (correctness/evidence — this is where we're losing):**
1. Independent raw→intervals oracle + interval-level diff gate (fix #2).
2. `content_id` → `Int64` (fix #1).
3. Dictionary → LEFT JOIN, or reload ON CLUSTER (fix #6).
4. Fix hour/day average densification (fix #3).
5. Ship + schedule cold compaction; make the view defensive (fix #8).
6. Sealed-run harness with checksums + git commit + `query_log.tsv`, clean tree.
7. Actually run the Redpanda→ClickPipes pipeline on Cloud end-to-end.

**Then (hedge the coin-flips + robustness):**
8. Parameterize foreground-resume / grace / gap; compute variants; pin to benchmark (#4).
9. Fix ghost-interval risk in live `session_intervals` (#7).
10. `dateDiff('second',...)` for the gap test; verify `VideoSessionStart` handling (#11, #5).
11. Consistent filter predicate across all UI queries; cheap dropdowns.

**Stretch (bank the differential):**
12. Meaningful ClickStack with a captured trace + read-cost badge on the dashboard.
13. Two-curve overcount hero visual + bystander-isolation open-session test.

---

## Per-competitor one-liners

- **nirad-sonyliv-concurrency** — the team to beat on evidence. Independent Python
  oracle gated interval-by-interval (0 diffs), 6 sealed run bundles with SHA-256 + git
  + `query_log.tsv`, real ClickStack OTLP exporter. Weak on read cost (delta+cumsum,
  checkpoint saved 0.4%, ~100K rows/query via a `FINAL` hot-view), incremental was
  *slower* than rebuild on this data, open-session test nearly vacuous, batch-only
  ingest.
- **sonyliv-clickathon-2026** — deepest correctness rigor (exact time-weighted metrics,
  hash-attested rebuild gates, policy-as-contract) and correct distinct-user
  concurrency. But **entirely chDB-on-laptop** (never ran on Cloud), **no UI**, and
  **mandatory integration 100% unbuilt**. Ships two contradictory answers (2,970 vs
  2,305) in one repo. Confirms the `content_id` signed-type trap.
- **phoenix-concurrency** — tight, well-validated (independent oracle 0-diff gate,
  excellent `test_open_sessions.sh` with bystander isolation, elegant retract/assert
  incremental). But **integration not started** (their own "largest scoring gap"),
  cumsum reads whole history, a real average bug in their graded `peak_average.sql`,
  and user-level concurrency is batch-only. Source of the same-ms determinism bug our
  plan references (ties → `leadInFrame` → engine-dependent wrong answers) — our
  (session,ms) collapse with deactivate>reactivate>neutral is the correct fix.
