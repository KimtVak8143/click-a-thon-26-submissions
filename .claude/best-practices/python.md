# Python best practices — Snorlax producer

> Referenced by CLAUDE.md. Applies to all Python in `Snorlax/`. Rules here codify
> what `Snorlax/producer/produce_events.py` already does well; follow them for new
> Python in this submission.

The only Python today is the event producer that streams simulated video-session
events into ClickHouse Cloud. Keep it a small, dependency-light, single-file script
until it genuinely needs more.

## 1. Version & tooling

- **Target Python 3.9+.** The producer's virtualenv (`Snorlax/producer/.venv`) is
  3.9, so avoid syntax newer than 3.9. `from __future__ import annotations` (see
  §2) lets you write `list | None` / `tuple[...]` hints on 3.9 without runtime
  errors.
- **Pin dependencies** in `requirements.txt` with lower bounds, as done today:
  ```
  clickhouse-connect>=0.7.0
  python-dotenv>=1.0.0
  ```
  Add a dep only when it earns its place; the producer needs just these two.
- **Always use a virtualenv** (`.venv/`), never the system interpreter:
  ```sh
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  ```
- **Formatting/linting (aspirational — not yet configured):** use `ruff` for lint
  and `black`/`ruff format` for formatting, and `mypy` for type checking. Until a
  config lands, match the existing style by hand and keep signatures typed so
  `mypy` can be turned on later with minimal churn.

## 2. Style

- Follow **PEP 8**: 4-space indent, `snake_case` functions/vars, private helpers
  prefixed `_` (`_rand_id`, `_row`, `_ts`, `_stop`).
- Start every module with `from __future__ import annotations` so annotations are
  lazy strings — this is what makes `list | None` and `tuple[...]` safe on 3.9.
- **Type-hint every function signature**, including return types. The state
  machine method is the template:
  ```python
  def next_event(self, now_ms: int) -> tuple[list | None, bool]:
  ```
  `main() -> int`, `_row(self, event_type: str, event: str, now_ms: int) -> list`,
  and `_rand_id(n: int = 12) -> str` are all fully annotated — keep it that way.
- **Docstrings**: a module-level docstring explaining what the script does and how
  to run it (the producer's opens with "Continuously stream dummy video-session
  events into ClickHouse Cloud." and includes a `Usage:` block), plus a short
  docstring on non-trivial functions/classes — see `Session`'s docstring and the
  `next_event`/`_ts` docstrings that explain the `None` (abandoned) return and the
  late-arrival stamping.
- **Constants in `UPPER_SNAKE_CASE`, grouped in a labelled config section** near
  the top, not scattered inline: `EVENTS_PER_SECOND`, `BATCH_SIZE`,
  `FLUSH_INTERVAL_SEC`, `COLUMNS`, `INACTIVE_STATES`, and the dummy-data
  dimensions (`CONTENT_IDS`, `PLATFORMS`, ...) all live under banner comments.

## 3. Configuration & secrets

- **Read all config from the environment** via `os.environ` / `os.getenv`, with
  sensible defaults for optional values and correct type coercion:
  ```python
  PORT = int(os.getenv("CLICKHOUSE_PORT", "8443"))
  SECURE = os.getenv("CLICKHOUSE_SECURE", "true").lower() in ("1", "true", "yes")
  EVENTS_PER_SECOND = int(os.getenv("EVENTS_PER_SECOND", "200"))
  ```
- **Fail fast on required vars** — index with `os.environ[...]` so a missing value
  raises immediately rather than silently defaulting:
  ```python
  HOST = os.environ["CLICKHOUSE_HOST"]
  PASSWORD = os.environ["CLICKHOUSE_PASSWORD"]
  ```
- **Load `.env` in development** with `python-dotenv`: call `load_dotenv()` once at
  import time, right after the imports.
- **Never commit real secrets.** `**/producer/.env` is gitignored; commit only
  `.env.example` as a template with placeholder values (host, `CLICKHOUSE_PASSWORD`,
  and the tuning knobs). Workflow: `cp .env.example .env`, then fill in
  credentials locally. Do not print secrets to logs.

## 4. Structure

- **Small, focused functions/methods.** Each does one thing: `_rand_id` makes an
  id, `_ts` computes an event timestamp, `_row` builds one row, `flush` writes a
  batch.
- **A `main() -> int` that returns an exit code**, invoked via the guard so the
  module stays importable (and unit-testable) without running:
  ```python
  if __name__ == "__main__":
      raise SystemExit(main())
  ```
- **Model stateful entities as classes.** `Session` holds one simulated session's
  identity and lifecycle `state`, and advances via `next_event()`. Keep such
  classes free of I/O so their logic stays pure and testable (see §8).
- Keep I/O (the ClickHouse client, batching, flushing) in `main`/its closures, not
  inside the domain classes.

## 5. Reliability & signals

- **Handle SIGINT/SIGTERM for graceful shutdown.** Install handlers that flip a
  flag rather than killing mid-batch:
  ```python
  _running = True

  def _stop(*_):
      global _running
      _running = False
  ...
  signal.signal(signal.SIGINT, _stop)
  signal.signal(signal.SIGTERM, _stop)
  ```
  The main loop runs `while _running:` and exits cleanly.
- **Always flush and close on exit** — use `try/finally` so buffered rows are not
  lost on Ctrl-C or error:
  ```python
  finally:
      flush()
      print(f"\nFlushed. Total inserted: {total:,} rows.", file=sys.stderr)
      client.close()
  ```
- **Batch, and flush on size OR time**, so latency stays bounded even at low
  volume:
  ```python
  if len(batch) >= BATCH_SIZE or (time.time() - last_flush) >= FLUSH_INTERVAL_SEC:
      flush()
  ```
- **Keep writes retry-safe.** Inserts feed an append-only landing table and the
  pipeline dedupes downstream (once-per-minute), so a retried batch must not
  corrupt results. Prefer idempotent, replayable writes over clever in-flight
  state.

## 6. ClickHouse client specifics

- Use **`clickhouse_connect`** (`clickhouse-connect>=0.7.0`), created once and
  reused: `client = clickhouse_connect.get_client(host=..., port=..., secure=...)`.
- Use **server-side async inserts** for high-frequency small batches, passed as
  settings on the insert:
  ```python
  settings = {"async_insert": 1, "wait_for_async_insert": 1}
  ```
  Keep `wait_for_async_insert=1` for a durable ack; drop to `0` only when you
  accept at-most-once ack semantics for throughput.
- **Column order is a contract**: the `column_names=COLUMNS` list passed to
  `client.insert(...)` must exactly match the order in which `Session._row()`
  builds each row (and the target table's INSERT columns). If you add a field,
  update `COLUMNS` and `_row` together, in the same order.
- **Close the client in `finally`** (`client.close()`) — see §5.

## 7. Logging

- For a single script, progress goes to **`stderr`** so `stdout` stays clean for
  piping:
  ```python
  print(f"Connected to {HOST}:{PORT} → {DATABASE}.{TABLE}", file=sys.stderr)
  print(f"\rinserted {total:>10,} rows | live sessions {len(sessions):>4}",
        end="", file=sys.stderr)
  ```
- For anything beyond a simple script (multiple modules, long-running services,
  structured output), switch to the **`logging`** module with levels and
  timestamps instead of `print`.
- Never log credentials or full row payloads at info level.

## 8. Testing

- **Keep pure logic separable and unit-testable.** The `Session` state machine
  (`start → playing → inactive sub-states → ended`) takes an integer `now_ms` and
  returns `(row, finished)` with no I/O — it can be driven directly in tests
  without a ClickHouse connection.
- **Make randomness deterministic in tests** with a fixed seed
  (`random.seed(...)`), since the simulator is random-driven (`random.random()`,
  `random.choice`). Seed before exercising a `Session` to get reproducible
  event sequences.
- Assert on invariants that matter downstream: the row's field order matches
  `COLUMNS`, `next_event` eventually returns `finished=True`, and the abandoned
  case returns `(None, True)`.
