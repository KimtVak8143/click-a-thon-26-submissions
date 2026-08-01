# SonyLIV Track — Solution Overview & Work Distribution (4 members)

*Foreground-only concurrency at streaming scale, on ClickHouse.*

## 1. The problem in one line

Count **only truly active viewers per minute** (foreground + playing + heartbeating), answer
**peak / average concurrency** at minute/hour/day grain with dimension filters
(platform, country, content, video type, …) at **dashboard latency**, and absorb **still-open
sessions + late heartbeats incrementally** — no full rescans of raw history.

The trap the whole problem exists to punish: counting session `start→end` as "watching."
An open app that is backgrounded, paused, or silent is **not** a viewer.

## 2. Target architecture (the shared mental model)

```
CSV (LFS)                ClickHouse Cloud (single team service)
─────────           ┌──────────────────────────────────────────────────────────┐
raw events  ──load──▶│ raw_events (MergeTree)      content_dim (dict / RMT)      │
                     │        │                                                   │
                     │        ▼  MV: sessionize + foreground filter              │
                     │ active_intervals  ([start,end) per session, enriched)     │
                     │        │                                                   │
                     │        ▼  interval → delta (+1 at start, −1 at end)       │
                     │ concurrency_deltas  (AggregatingMergeTree)                │
                     │        │                                                   │
                     │        ▼  cumulative sum over time, per dimension group   │
                     │ SERVING: minute-grain concurrency  ◀── dashboard/queries  │
                     └──────────────────────────────────────────────────────────┘
                                     │
                     ClickStack (pipeline observability)  and/or
                     LibreChat + ClickHouse MCP (chat over concurrency)
```

**The core idea — interval-to-delta:** turn each *active* interval into `+1` at its start minute
and `−1` at its end minute, bucketed per (minute × dimensions). Concurrency at any minute =
**cumulative sum of deltas up to that minute**. Peak over a range = `max` of the running sum;
average = mean. This avoids exploding every session into per-minute rows and avoids recomputing
overlap from raw history on every query.

**Two views to build and compare (the docs explicitly ask for both):**
- **Session-aware:** derive active ranges *within* each `video_session_id`, then count overlap.
- **Session-independent:** count active foreground viewers directly from event state, no session reconstruction.
Comparing them is how we validate correctness and argue trade-offs.

## 3. The hard sub-problems (where points are won)

1. **Defining an active interval** — heartbeat every 60s, so a gap > ~90s = inactive (timeout);
   `AppBackgrounded`/pause closes active, `AppForegrounded`/`VideoPlay` reopens. Background/foreground
   events are **not guaranteed**, so heartbeat-gap logic must be the safety net.
2. **Representation choice** — interval arrays vs normalized intervals vs pre-aggregated minute deltas
   vs hybrid. We default to **delta model for serving**, keep intervals for recent/open data.
3. **Peak/avg without scanning raw** — cumulative-sum over a compact serving table; per-dimension-group
   cumsum (peak minute differs per filter combination — see the 300K/200K/50K example in the spec).
4. **Filter-friendliness** — ordering key + AggregatingMergeTree so any dimension subset stays fast.
5. **Open sessions + late arrivals** — watermark; recent tier stays as intervals and recompacts,
   history is finalized deltas. Must update **incrementally**, not rebuild.

## 4. What "great" is graded on

Correct (foreground-only, matches hidden ground truth) · Fast (reads serving layer, not raw) ·
Update-friendly (incremental open-session absorption) · Explained (defend the trade-offs) ·
**The unseen day** (fresh day released in final hours — run it through the pipeline, submit answers
+ latencies + evidence. No pipeline evidence, no credit).

## 5. Work distribution — 4 members

The dependency spine is **A → B → C**, with **D** integrating in parallel. B and C are the
technical core and should pair tightly. Rough split of effort: A 20%, B 30%, C 30%, D 20%.

### Member A — Data & Schema (foundation, unblock everyone fast)
- `git lfs install && git lfs pull` — **the CSVs are currently LFS pointer stubs, not real data.** Do this first.
- Provision the team's ClickHouse Cloud service (event credits). One shared service.
- Design & load `raw_events` (MergeTree) + `content_dim` (dictionary or ReplacingMergeTree).
- Choose ordering keys / partitioning for raw + content; type the columns from `dataset_details.md`.
- Data-quality pass: event ordering per session, timestamp sanity, null/dup handling, cardinalities
  per dimension. Hand B a clean, queryable base + a short "data facts" note.
- **Deliverable:** loaded tables + DDL + data profiling notes. **Owns:** ingestion correctness.

### Member B — Concurrency Model / core algorithm (the crux)
- Define the **active-interval derivation**: sessionize per `video_session_id`, apply foreground rules
  (play/pause, background/foreground, heartbeat-gap timeout, session end).
- Build `active_intervals` via materialized view; then the **interval→delta** transform into
  `concurrency_deltas` (AggregatingMergeTree, dimensions in the key).
- Implement **both** session-aware and session-independent variants.
- Decide representation + tiering (intervals for recent/open, deltas for history).
- **Deliverable:** the serving table(s) + MV chain that C queries. **Owns:** correctness of "active."

### Member C — Serving, Queries & Performance
- Write the **benchmark query set**: minute/hour/day peak & average concurrency, with dimension filters
  — using per-group cumulative sum (peak minute varies by filter combo).
- Tune for **dashboard latency**: verify queries read the serving layer, not raw; measure `bytes read`,
  not just wall-clock. Iterate ordering keys / projections with B.
- **Open-session & late-arrival tests:** prove the serving layer absorbs updates incrementally.
- Validate session-aware vs session-independent agree; build the correctness harness.
- **Deliverable:** benchmark queries + latency report + correctness comparison. **Owns:** fast & right.

### Member D — Integration, Observability, Demo & Submission
- Integrate **at least one** required tool, meaningfully:
  - **ClickStack** → observe our own pipeline (ingestion lag, query performance), + optional
    LLM/ClickStack concurrency-decline alerting (asset ended / system issue / low engagement).
  - and/or **LibreChat + ClickHouse MCP** → chat layer ("peak concurrency on Android last hour?").
- Minimal **concurrency-over-time visualization** for the demo (curve builds as sessions open/close,
  apply a filter, instant minute-grain view).
- **Own the unseen-day run:** when the sealed day drops, push it through A→B→C, capture answers +
  latencies + query logs/traces as evidence, assemble the submission.
- Write the **trade-off document** (representation, ordering keys, tiering, why session-aware vs -independent).
- **Deliverable:** integration + demo + submission package. **Owns:** the story judges see.

## 6. Suggested timeline (24h)

| Phase | A | B | C | D |
|---|---|---|---|---|
| 0–3h | LFS pull, provision CH, load tables | design active-interval rules with C | draft benchmark queries against dummy | stand up ClickStack/LibreChat |
| 3–10h | data profiling, fix load issues | build `active_intervals` + delta MV | first real queries + latency baseline | MCP/chat wiring, viz skeleton |
| 10–18h | support, 100× scale reasoning | both views, open-session/tiering | tune ordering keys, correctness harness | concurrency-decline alert / demo |
| 18–22h | **unseen day loads here** | verify pipeline on unseen day | benchmark answers + latencies on unseen | capture evidence, assemble submission |
| 22–24h | — | trade-off review | final latency report | demo dry-run + writeup |

## 7. Key decisions to lock early (whole team, hour 1)

- Heartbeat timeout threshold (start: 90s, since heartbeat = 60s).
- Late-arrival / watermark tolerance and open-session cutoff.
- Which integration tool is the "meaningful" one (don't spread thin).
- Serving-table dimension set + ordering key (design for *more* dimensions than given).

> Everything must run in **ClickHouse** as the primary engine. Design for 100× — no full rescans,
> no per-minute explosion of all history. Defensible trade-offs beat a lucky benchmark.
