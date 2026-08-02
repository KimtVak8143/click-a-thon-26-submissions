# Product Analytics Knowledge Base

**Version:** 1.0  
**Last Updated:** 2026-08-02  
**Purpose:** Reusable framework for event analysis, DDL review, data quality, and evidence-backed product recommendations.

## Event schema analysis

Every event should use a consistent envelope:

- Identification: `event`, unique `id`, UTC `timestamp`, `user_id`, and the narrowest stable workflow identifier such as `application_id`.
- Technical context: `device_type`, `os`, `app_version`, and `client_lib`.
- Geographic context: `geoip_country_code` and optional `city`.

Prefer descriptive, past-tense names such as `express_checkout_shown` and
`payment_confirmed`. Avoid vague or inconsistent names. Flatten frequently filtered dimensions;
keep truly optional, cohesive structures nested.

Evidence: `analytics_kb:v1:event_envelope`.

## Data-quality policy

Validate expected event coverage, ordering, event-ID uniqueness, identifier relationships, types,
null rates, enums, numeric ranges, UTC timestamps, and schema drift before interpreting metrics.
Specifically monitor null OS values, inconsistent device labels, duplicate events, missing funnel
steps, timezone inconsistencies, unexpected fields, and add-on value/currency inconsistencies.

Data-quality failures must be distinguished from product behavior. A suspected anomaly is not a
recommendation until instrumentation health is established.

Evidence: `analytics_kb:v1:data_quality`.

## Funnel and anomaly analysis

For ordered funnels report each step population, step-to-step conversion, overall conversion,
drop-off, and time-to-convert. Always name the entity grain and denominator. Segment only on
observed dimensions and require sufficient sample size.

Investigate volume z-scores beyond 3, sudden conversion changes, platform or geography
divergence, sub-second completions, long delays, timestamp inversions, new categorical values,
null-rate increases, orphan completion events, and duplicate critical events. Thresholds are
diagnostic defaults, not proof of causality or universal business targets.

Evidence: `analytics_kb:v1:funnel_analysis`, `analytics_kb:v1:anomaly_detection`.

## ClickHouse DDL review

- Keep raw event facts separate from computed metrics.
- Use `DateTime64(3, 'UTC')` for event time.
- Partition time-series fact tables by a bounded date expression such as `toYYYYMM(timestamp)`.
- Put the primary workflow key, event name, and timestamp in the sorting key according to query patterns.
- Use `LowCardinality(String)` for stable, low-cardinality dimensions.
- Flatten frequently filtered fields; retain irregular event properties only when a typed column is unsafe.
- Preserve raw event identity and ingestion time for deduplication and audit.
- Do not copy PostgreSQL/MySQL `PRIMARY KEY`, `JSONB`, or secondary-index examples literally into ClickHouse; translate the analytical intent into ClickHouse engines, sorting keys, projections, or skip indexes.

Evidence: `analytics_kb:v1:ddl_review`.

## Metric derivation

- Conversion rate: converters / eligible entities.
- Incremental conversion: next-step entities / current-step entities.
- Overall conversion: final-step entities / first-step entities.
- Adoption rate: feature users / eligible users.
- Actions per user: actions / unique users.
- Average order value: revenue / completed orders.
- Attach rate: orders with add-on / eligible orders.
- Recovery rate: recovered entities / abandoned entities.
- K-factor: new users attributed to shares / original sharers.
- Data completeness: non-null expected values / expected values.

Every metric definition must include numerator, denominator, entity grain, time window,
deduplication, zero-denominator behavior, and currency policy when relevant. Metrics should be
actionable, understandable, auditable, timely, comparable, and segmentable.

Evidence: `analytics_kb:v1:metric_formulas`, `analytics_kb:v1:metric_quality`.

## Segmentation and workflow

Candidate segments include device, geography, destination, user type, time, behavior, and
acquisition channel. A dimension is usable only when it is observed and normalized. Start every
new-feature analysis with instrumentation and data-quality validation, then establish baselines,
analyze funnels and segments, and finally propose testable actions.

For anomalies: verify the data, determine scope, check external factors and deployments, compare
historical behavior, and document evidence and follow-up. Never turn a known issue or correlation
into a causal claim without supporting query evidence.

Evidence: `analytics_kb:v1:segmentation`, `analytics_kb:v1:analysis_workflow`.

## Recommendation evidence rule

Every numerical claim must be present in executed SQL output and cite its query evidence. General
knowledge may guide which question to ask and how to interpret a result, but it cannot supply a
number, establish causality, or replace feature-specific evidence.

Evidence: `analytics_kb:v1:evidence_policy`.
