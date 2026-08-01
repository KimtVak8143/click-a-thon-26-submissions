# SonyLIV — Gap Analysis

*What the problem statement / README require, but `DESIGN_PLAN.md` and `SOLUTION_OVERVIEW.md` either omit or under-specify.*

## Significant gaps (correctness-affecting)

### 1. The session-independent model is essentially missing from the plan
Both `PROBLEM_STATEMENT.md` (line 18) and `README_START_HERE.md` (lines 34–38, 59) require **two** views — *session-aware* AND *session-independent* — plus an explicit **comparison** to validate accuracy and trade-offs. `SOLUTION_OVERVIEW.md` names both (§2) and assigns "implement both" to Member B, but `DESIGN_PLAN.md` only designs one pipeline (interval→delta, the session-aware path). There is no design for computing "active foreground viewers directly from event state, no session reconstruction," and no design for how the two are reconciled/compared. **Biggest hole.**

### 2. Paused state is under-specified — and the docs contradict each other
The problem calls out three inactive causes: *heartbeat missing, paused, backgrounded* (line 22). `DESIGN_PLAN.md §3` only handles heartbeat-gap and `AppBackgrounded` — no pause handling. Its event list (lines 15–16) has **no pause event**, yet `SOLUTION_OVERVIEW.md §3` says "AppBackgrounded/pause closes active." If a paused player keeps heartbeating, the gap rule won't catch it, so pause needs an explicit rule/state field. Resolve whether a pause event / playback-state marker exists (via `dataset_details.md`, which isn't in the folder).

### 3. `VideoError` handling is undefined
It's in the event stream (`DESIGN_PLAN.md` line 16) but neither doc says whether an error terminates an active interval, marks inactivity, or is ignored. Directly affects active-range computation.

### 4. Deduplication of late/repeated events is not in the model
`README_START_HERE.md` step 3 (line 44) explicitly requires "deduplicate late or repeated events." `DESIGN_PLAN.md` never mentions dedup; `SOLUTION_OVERVIEW.md` only gestures at "null/dup handling" as a Member A chore. Dedup logic (ReplacingMergeTree? by event id?) should be part of the pipeline design since duplicate heartbeats corrupt interval derivation.

## Medium gaps (scope / coverage)

### 5. Hour/day grain roll-up is not designed
Problem asks peak & average at **minute/hour/day** grain (lines 24, 26). `DESIGN_PLAN.md` designs only the minute curve; how hour/day peak (max-of-minutes) and average roll up from it — and whether that needs additional serving tables — isn't addressed.

### 6. Average-concurrency semantics are undefined
Peak has the worked 300K/200K/50K example, but "average" has no denominator defined (mean over all minutes in the window including zeros, vs. only active minutes). This changes the answer against ground truth.

### 7. The provided benchmark query set is treated as something to author
The problem says the benchmark set is **given** ("A benchmark query set… the fixed concurrency questions your system will be evaluated on," line 32). `SOLUTION_OVERVIEW.md` Member C says "**Write** the benchmark query set." The plan should ingest/run the provided set and its answer format, not invent its own.

### 8. Content join is described as a plain dimension add, not a real-time enriched join
`README_START_HERE.md` (line 32, and the content-level-concurrency aggregation, line 52) stresses real-time join and **join consistency**. `DESIGN_PLAN.md` reduces it to step 5 ("content join for title/video_type/category"); join consistency and dedup of the ~33K content table aren't discussed.

## Minor / worth noting

- **Latency SLA is unquantified** — "dashboard-grade latency" with no target number to design/test against.
- **Langfuse** is never considered (fine — only one integration is required — but worth a sentence saying why it was dropped).
- **User-level vs session-level concurrency** is correctly flagged as open (`DESIGN_PLAN.md §10`) — good, just needs a decision.
- **Missing referenced files**: `dataset_details.md` and the `data/` CSVs are referenced throughout but aren't in the `SonyLiv/` folder, so several open decisions (GRACE value, event schema, whether a pause/error state field exists) can't be closed until those are present.
