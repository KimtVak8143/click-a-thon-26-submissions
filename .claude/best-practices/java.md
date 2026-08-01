# Java Best Practices (Snorlax / SonyLIV Concurrency)

Referenced by CLAUDE.md. **No Java exists in this repo yet** — this is forward-looking guidance for any future JVM component (a CSV-reader → Redpanda producer service, a ClickPipes-adjacent ingestion service, or a Spring Boot API in front of the `concurrency_now` serving view). Align every choice with the pipeline decisions in `Snorlax/plan/PLAN.md`: Redpanda transport, ClickPipes ingest, key = `video_session_id`, **ms-epoch** timestamps, foreground-only state machine.

## 1. Version & build

- Target **Java 21 (LTS)**. Do not depend on preview features in shipped code.
- Use **Gradle (Kotlin DSL)** or **Maven** — pick one per service and stay consistent.
- **Lock the dependency set:** Gradle version catalog (`libs.versions.toml`) or Maven `dependencyManagement` / `<dependencyManagement>` BOMs. Commit lockfiles so builds are reproducible.
- Keep the module thin: a producer service needs `kafka-clients`, a JSON lib (Jackson), SLF4J + a binding, and test deps. Resist pulling a framework you don't need.

## 2. Style & formatting

- Follow **Google Java Style** as the chosen standard, with **4-space indentation** (no tabs).
- Format automatically with **Spotless** wired to **google-java-format**; make `spotlessCheck` part of CI so unformatted code fails the build.
- Lint with **Error Prone** (compile-time bug patterns) and optionally **Checkstyle** for style rules Spotless doesn't cover.
- **One top-level class per file.** Package names all lowercase, reverse-domain, e.g. `com.snorlax.sonyliv.producer`.
- Mark fields, params, and locals `final` where practical — it documents intent and helps the reader (and Error Prone) reason about mutation.

## 3. Language idioms

- **Prefer immutability.** Default to immutable objects and unmodifiable collections; only introduce mutable state where measured need exists.
- Use **`record`** for DTOs and events. A video-session event is a natural record:

  ```java
  public record SessionEvent(
      String videoSessionId,
      String eventType,     // VideoPlay, AppBackgrounded, AdPause, ...
      long eventTimestamp,  // ms epoch — see §6, do NOT pre-convert
      String country,
      String platform) {}
  ```

- Return **`Optional<T>`** instead of `null` for "maybe absent" results. Do not use `Optional` for fields or method parameters.
- Use **`var`** for locals when the type is obvious from the right-hand side; spell out the type when it aids clarity.
- Use **enhanced `switch`** (arrow form, exhaustive) over `if/else` ladders.
- Use **sealed types** for closed hierarchies — e.g. state-machine states or event kinds — so the compiler enforces exhaustiveness. Combined with an exhaustive `switch`, adding a new state becomes a compile error until every site handles it:

  ```java
  sealed interface WatchState permits Watching, Paused, Closed {}

  int delta(WatchState s) {
      return switch (s) {          // no default — compiler checks exhaustiveness
          case Watching w -> +1;
          case Paused p   -> -1;
          case Closed c   ->  0;
      };
  }
  ```

- Use **streams** for clear transforms; use plain loops when they read better or in hot paths where allocation matters.

## 4. Null-safety & validation

- **Validate at boundaries.** Check inputs where data enters the service (deserialization, HTTP handlers, config load) — trust internal invariants after that.
- Use `Objects.requireNonNull(x, "x")` for constructor/method preconditions on things that must never be null.
- **Never return `null`** from a public method; return `Optional`, an empty collection, or throw.
- Reject malformed events early (missing `videoSessionId`, non-positive timestamp) rather than shipping bad rows downstream to `events_incoming`.

## 5. Concurrency

- Prefer **`java.util.concurrent`** (`ExecutorService`, `CompletableFuture`, concurrent collections) over raw `Thread` / `synchronized` blocks.
- Pass **immutable messages** between threads/executors — records make this easy and eliminate shared-mutable-state bugs.
- For IO-bound ingestion (tailing files, network sends), use **virtual threads** (Java 21): `Executors.newVirtualThreadPerTaskExecutor()`. Keep CPU-bound work on a bounded platform-thread pool.
- Install a **graceful shutdown hook** that stops intake, drains in-flight work, and **flushes the producer** before exit — this mirrors the Python producer's SIGINT flush-on-exit:

  ```java
  Runtime.getRuntime().addShutdownHook(new Thread(() -> {
      running.set(false);
      producer.flush();   // don't drop buffered records
      producer.close();
  }));
  ```

## 6. Kafka / Redpanda producer specifics

- **Enable the idempotent producer:** `enable.idempotence=true` (implies `acks=all`, safe retries). Prevents duplicate records on retry.

  ```java
  var props = new Properties();
  props.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, brokers);   // from config, §7
  props.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);     // acks=all + safe retries
  props.put(ProducerConfig.LINGER_MS_CONFIG, 20);               // batch window, §6
  props.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
  props.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);

  // key = video_session_id → same session stays on one partition, in order
  var record = new ProducerRecord<>(topic, event.videoSessionId(), json);
  producer.send(record, (meta, ex) -> { if (ex != null) log.error("send failed", ex); });
  ```

- **Key every record by `video_session_id`** — matches PLAN.md and keeps a session's events on one partition and in order.
- **Batch for throughput:** set `linger.ms` (e.g. 10–50ms) and a healthy `batch.size` so the producer groups records; balance against latency needs.
- **Payloads must be `JSONEachRow`-compatible** for ClickPipes → `events_incoming`: one JSON object per record, field names matching the landing table.
- **Timestamps are ms-epoch `long`s — do NOT pre-convert to `DateTime`.** ClickPipes/ClickHouse own the cast per the plan's contract; sending pre-converted strings breaks it.
- **Flush on shutdown** (see §5) so buffered records reach Redpanda before the process dies.

## 7. Config & secrets

- **Externalize all config** via environment variables or a typed config object; no hardcoded brokers, topics, or endpoints.
- **Fail fast** at startup on any missing required config (broker list, topic, source path) with a clear message — mirror the Python producer's fail-fast approach. A service that starts misconfigured is worse than one that refuses to start.
- **Never commit secrets.** Keep credentials in env / a secrets manager; `.gitignore` any local `.env`.

## 8. Errors & logging

- Log through **SLF4J**; pick one binding (Logback) per service.
- **Never call `printStackTrace()`** and never swallow exceptions silently — log with context or rethrow.
- Throw **meaningful exceptions** (specific type + message with the offending value); don't leak `RuntimeException` with no detail.
- Use **structured logging** (key-value / JSON) so ClickStack and log tooling can parse ingestion lag, send failures, and record counts.

## 9. Testing

- Use **JUnit 5** for all tests.
- Keep **pure logic unit-testable** — the foreground-only state machine (90s gap / 60s grace, `deactivate > reactivate > neutral` collapse) should be a plain, dependency-free class covered by fast unit tests. This is the highest-value code to test.
- Use **Testcontainers** for integration tests against real **Redpanda** and **ClickHouse** — verify the producer's `JSONEachRow` payloads actually land in `events_incoming` and flow through to `events_raw`.
- Cover edge cases the plan calls out: out-of-order events, same-ms ties, duplicate records, long heartbeat silence, and still-open sessions.

## 10. Fitting the pipeline

Whatever JVM component gets added, hold the contracts PLAN.md already froze:

| Component | Must honor |
|---|---|
| **CSV-reader → Redpanda producer** (Track B) | key = `video_session_id`; `JSONEachRow` payloads matching `events_incoming`; ms-epoch timestamps left uncast; idempotent producer; flush on SIGTERM/shutdown |
| **ClickPipes-adjacent ingestion** | never mutate timestamps or field names — ClickHouse casts on landing; dup-tolerant (the state machine collapses per `(session, ms)` and ignores repeats) |
| **Spring Boot API over `concurrency_now`** | read only the serving view (never `events_raw`); pass filters as lenient string params; return `filter → sum → max/avg` shapes; no business logic that re-derives concurrency |

Keep any concurrency logic (the 90s-gap / 60s-grace state machine) in one pure, framework-free class so it stays unit-testable and mirrors the SQL definition exactly.
