Use simpler subagents as much as possible

## Git operations — never run them directly
Do NOT execute any git operation yourself (no commits, branches, merges, pushes,
tags, resets, etc.). Instead, write the exact git command(s) to a temporary file
(e.g. `git-commands.sh`) and ask the user to execute them. Once the user confirms
they have run the commands, delete the temporary file.

# Snorlax submission — SonyLIV foreground-only concurrency

Active code lives in `Snorlax/`. Do NOT put implementation code in `SonyLiv/`
(that folder holds the problem statement, design docs, and GAP_ANALYSIS.md only).

## Coding standards (read before writing code)
Follow the language best-practices docs in `.claude/best-practices/` — consult the
relevant one before writing or reviewing code, and keep them current when a new
convention is adopted:
- SQL / ClickHouse → [`.claude/best-practices/sql.md`](best-practices/sql.md)
- Python → [`.claude/best-practices/python.md`](best-practices/python.md)
- Java (future JVM components) → [`.claude/best-practices/java.md`](best-practices/java.md)

## Keep docs current
`Snorlax/README.md` is the submission's user-facing doc — update it whenever the
project's behavior, run steps, or layout change. Reflect any new locked decisions
here in this CLAUDE.md too, and keep the run order in both files in sync with
`plan/PLAN.md`.

- ClickHouse is the engine. Database: `sonyliv_concurrency`.
- Design: absolute concurrency per `(dims, minute)` (no cumsum / carry-in).
  Serving = `concurrency_cold_abs` ∪ `concurrency_hot_abs` via the
  `concurrency_now` view. See `Snorlax/plan/PLAN.md` for the locked decisions
  (90s gap / 60s grace, overlap minute semantics, once-per-minute dedupe).

## Two concurrency approaches (the problem requires both + a comparison)
- **Session-aware** — reconstruct per-session truly-active intervals via the
  state machine (`session_intervals`), expand to minutes, count distinct
  sessions. Files: `schema/schema.sql`, `schema/backfill_history.sql`;
  standalone comparable table `concurrency_sa_abs` in
  `schema/approach_session_aware.sql`.
- **Session-independent** — derive per-event foreground state directly (no
  per-session interval reconstruction), expand active segments to minutes,
  count distinct sessions. Table `concurrency_si_abs` in
  `schema/approach_session_independent.sql`.
- **Comparison** — `schema/compare_approaches.sql` asserts the two agree
  (and match `concurrency_now`) per `(dims, minute)`; expect zero mismatches.

Both share ONE active definition; `uniqExact` per minute makes interval-merging
(session-aware) vs not-merging (session-independent) irrelevant to the count.

## Tunable knobs
`schema/config.sql` is the single place for tunable parameters, exposed as SQL
UDFs (they inline to constants, so they work inside `toStartOfInterval`/`INTERVAL`
where a config table would fail the constant requirement). Run it FIRST and
re-run after any change, then rebuild.
- `cfg_bucket_seconds()` — time-bucket width (was fixed 1 min = 60). Change for
  30s / 5-min / hourly buckets. Serving column is still named `minute`.
- `cfg_heartbeat_seconds()` + `cfg_missing_heartbeat_buffer_seconds()` — the
  gap tolerance. Derived `cfg_gap_timeout_seconds()` = heartbeat + buffer
  (default 60 + 30 = old 90s). Raise the buffer to bridge more missing beats.
Non-UDF-able knobs (hot window, refresh cadence, TTL) are documented in
`config.sql` and set at their source literals.

## Dimensions
Serving splits into two keyed tables:
- **Core** (lean, fast, live hot/cold): `(country, platform, video_type, category,
  minute, content_id)` — `concurrency_cold_abs`/`hot_abs` → `concurrency_now`.
- **Extended** (drill-down, offline/scheduled build): core key + `app_version,
  player_version, audio_language, subtitle_language` → `concurrency_ext_abs`
  (`approach_extended_dims.sql`). Kept separate per PLAN §9 Fix #7 so the core
  path stays fast. It rolls back up to the core counts exactly (cross-check in
  that file).
The 4 extended dims are **normalized at ingest** (`config.sql` `norm_lang` /
`norm_dim`): `hin/HIN/hin-hindi → hin`, empty → `unk`. Core content dims
(`video_type`/`category`) are left as-is to avoid ground-truth divergence.
Note: `country` is single-valued (`india`) in the sample data.

## Run order (offline / backfill)
`config.sql` → `schema.sql` → load events (`load_sample_csv.sql` or
`seed_sample.sql`) → `backfill_history.sql` → `approach_session_aware.sql` →
`approach_session_independent.sql` → `approach_extended_dims.sql` →
`compare_approaches.sql` → `verify.sql`.

SQL not yet executed on a live ClickHouse — expect minor engine fixes on first run.
