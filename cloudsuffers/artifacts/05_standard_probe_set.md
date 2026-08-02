# 5. Standard probe set

The following prompts are the standard probe set the judges will use to exercise the Analytics Agent against the existing tables. The outputs should be captured and linked into the submission bundle.

## Probe 1

Prompt:

> Analyze the existing funnel and surface the most important issues, with the why.

Expected output:

I can do this, but I don’t yet have the actual funnel data or flow to analyze.

Please share one of the following:

A screenshot or diagram of the funnel
Funnel step metrics, e.g.:
Visitors / sessions
Landing page views
Signup starts
Signup completions
Activation events
Checkout starts
Purchases
Retention / repeat usage
A table with conversion rates by step
Segment breakdowns, if available:
Channel
Device
Geography
New vs returning users
Paid vs organic
Plan / product type
Once you share it, I’ll identify the highest-priority issues and explain why they matter.

In the meantime, here’s the structure I’ll use to analyze it:

Area	What I’ll look for	Why it matters
Top-of-funnel quality	High traffic but low engagement or conversion	Indicates weak targeting, poor messaging, or low-intent acquisition
Landing page performance	Large drop-off before the first meaningful action	Usually means the value proposition, CTA, load speed, or trust signals are weak
Signup / lead capture	Users start but don’t complete	Suggests friction, unnecessary fields, unclear benefit, or privacy concerns
Activation	Users sign up but don’t reach the “aha” moment	Often the biggest growth blocker because acquisition is wasted if users don’t experience value
Checkout / payment	High intent users abandon late	Points to pricing objections, payment friction, surprise fees, lack of trust, or weak urgency
Segment gaps	Certain channels/devices convert much worse	Helps prioritize fixes where the biggest volume or revenue upside exists
Retention / repeat behavior	Users convert once but don’t come back	Indicates a product-value, onboarding, expectation, or lifecycle marketing issue
Measurement	Missing or inconsistent tracking	Makes it hard to diagnose problems and can lead to the wrong optimization decisions
The final output will look something like:

Issue: Major drop-off between X and Y

Why it matters: This is the largest volume leak and fixing it could improve downstream revenue significantly.

Likely causes: Messaging mismatch, unclear CTA, friction, low-intent traffic, or technical issues.

Recommended next steps: Segment by device/channel, review session recordings, A/B test CTA/value prop, check page speed.

Issue: Signup completion rate is weak

Why it matters: Users have shown intent, so improving this step usually has high leverage.

Likely causes: Too many fields, unclear payoff, account-creation friction, lack of trust.

Recommended next steps: Reduce form fields, add social login, clarify benefit, test progressive profiling.

Send the funnel and I’ll give you a prioritized diagnosis.

## Probe 2

Prompt:

> Where are we losing conversions, and for which segments (device / geo / destination)?

Expected output:

Copy thoughts to clipboard

I found two candidate datasets in the atlys-context-compiler service (4bd64096-b75f-497a-aaf7-ad63de0beabf) that both model a visa-application conversion funnel (application_started → auth_completed → destination_card_clicked → document_uploaded → purchase_completed), with identical row counts on the shared tables — but each database has different bonus tables:

clickathon1 also has: express_checkout_events, abandoned_checkout_recovery_events, visa_status_sharing_events, recipient_status_sharing_events
clickathon2 also has: landing_page_scrolled, pay_now_clicked
Since these look like two separate hackathon submissions built on the same base funnel data, I want to confirm scope before running the drop-off analysis so I don't mix an incomplete picture.

Asked
Which dataset should I analyze for the conversion funnel drop-off?

Both `clickathon1` and `clickathon2` databases (service atlys-context-compiler) contain the same core funnel tables (application_started, auth_completed, destination_card_clicked, document_uploaded, purchase_completed) with identical row counts, but each has different extra checkout-related event tables.

You answered: clickathon2 (Recommended) — includes pay_now_clicked + landing_page_scrolled, giving a fuller pre-payment funnel


Thoughts
Copy thoughts to clipboard

Good — using clickathon2. Let me examine the funnel entity keys, hygiene columns (duplicates/backfills), data freshness, and null rates before computing drop-off, since this determines whether I count by application_id or raw events.

Ran run_select_query

Ran run_select_query
in ClickHouse



Thoughts
Copy thoughts to clipboard

Good — application_id is the true funnel entity (each stage table is 1:1 or fans-out on application_id, while user_id turns out to be per-event, not a persistent identity here). Let me confirm the actual step order chronologically, then build the funnel drop-off with segment breakdowns.

Ran run_select_query

Ran run_select_query
in ClickHouse





Thoughts
Copy thoughts to clipboard

Ran run_select_query

Ran run_select_query
in ClickHouse





Thoughts
Copy thoughts to clipboard

Now I have the overall funnel. The dominant leak is between auth_completed → document_uploaded (86.8% drop). Let me break this down by device, geo, and destination.


Used 3 tools
— ClickHouse

Thoughts
Copy thoughts to clipboard

Ran run_select_query

Ran run_select_query
in ClickHouse



Thoughts
Copy thoughts to clipboard

Where conversions are lost — Atlys visa-application funnel (clickathon2)
Funnel & definition used: I treated application_id as the funnel entity (it's the stable key across all stage tables — user_id in this dataset is actually per-event, not persistent, so it can't be used to track a person across steps). I verified the real chronological order with sampled timestamps: destination_card_clicked → auth_completed/application_started → document_uploaded → pay_now_clicked → purchase_completed. "Conversion" = an application that reaches purchase_completed.

Overall funnel (154,413 applications started)
Stage	Reached	Step conversion	Step drop
application_started	154,413	— (base)	—
auth_completed	154,413	100.0%	0%
document_uploaded	20,446	13.2%	▼ 86.8%
pay_now_clicked	14,739	72.1%	▼ 27.9%
purchase_completed	7,054	47.9%	▼ 52.1%
Overall conversion (started → purchased): 4.57%

The dominant leak, by far, is between auth_completed and document_uploaded — 6 out of 7 applicants who authenticate never upload a document. The second-largest leak is pay_now_clicked → purchase_completed (52% drop, likely a payment-failure point). document_uploaded → pay_now_clicked (28% drop) is the third.

Is the biggest leak segment-specific, or systemic?
By device (auth→docu step is nearly flat everywhere ∼13%, but a real gap opens later at docu→pay):

device_type	started	docu→pay %	pay→purchase %	overall conversion
iOS	63,520	75.5%	49.9%	5.03%
Desktop	10,655	74.1%	46.5%	4.68%
web-user-b2c	30,611	72.8%	46.1%	4.36%
Android	49,627	66.8%	46.4%	4.08% (weakest)
→ Android is the one real device-specific gap: once an Android user uploads documents, they're ∼9 points less likely to click "pay now" than iOS (66.8% vs 75.5%). That's the most actionable device finding — worth a UX/perf audit of the Android review→payment screen.

By geography (top markets by volume):

geo	started	auth→docu %	overall conversion
IN (India, largest market)	86,506	13.2%	4.38%
AE	21,630	13.2%	5.38% (best)
US	7,756	12.4%	4.36%
SG	2,329	12.3%	3.69% (weakest)
GB / AU / SA / QA / OM	3.2k–6.2k	12.6–13.7%	4.5–4.8%
→ Geo differences are modest (3.7%–5.4% range) — no market is dramatically broken; AE converts best, Singapore and India (despite being the largest market) trail slightly.

By destination (top 20 by volume, full list available on request):

destination	started	overall conversion
FR, OM, JP, TR	3.2k–4.3k	4.8–5.3% (best)
ID (Indonesia)	13,551	4.23% (weakest, large volume)
TH	9,808	4.31%
AE, US (largest destinations)	15k–25k	∼4.4%
→ Destination conversion is also fairly flat (4.2%–5.3%); nothing jumps out as a destination-specific failure, though Indonesia and Thailand (high-volume) trail the pack slightly.

Bottom line
The auth→document-upload leak (87% loss) is systemic, not segment-specific — it hits iOS/Android/Desktop/Web and every geo/destination roughly equally (∼12–14.5%). This points to a product/flow problem (e.g., document-capture friction, unclear next step, or a drop-off screen right after auth) rather than a device- or market-targeted fix.
Android has a distinct, fixable gap at document_uploaded → pay_now_clicked (66.8% vs 75–76% elsewhere) — the highest-leverage segment-specific finding.
pay_now_clicked → purchase_completed loses ∼52% everywhere — likely a payment/checkout issue (declines, timeouts) worth investigating in payment logs, independent of device/geo/destination.
Caveats / assumptions
I did not exclude is_back_filled (∼2%) or rows carrying a duplicate_id (∼3%) — these are small enough not to change the story, but a stricter production read should filter them.
auth_completed shows 100% reach from application_started, which likely means auth is a required/automatic gate at the moment an application record is created (not a step users can visibly abandon) — so the real "browse → apply" drop-off (via landing_page_scrolled/search_typed/destination_card_clicked, which have fewer unique application_ids: 77K/92K/154K) isn't captured by this application-level funnel and would need session-level tracking to analyze.
Data timestamps run through 2026-07-01 (synthetic/test dataset) — that is the latest date present and what freshness is anchored to.
Source: raw exploration (clickathon2.application_started/auth_completed/document_uploaded/pay_now_clicked/purchase_completed, joined on application_id) · Freshness: max event timestamp 2026-07-01 · Key filters: no population exclusions applied (guest/enterprise/backfilled/duplicate rows included) · Confidence: medium — funnel logic and joins are verified, but definitions of "conversion" and stage order were inferred from data, not confirmed against a product spec.

Would you like me to (a) turn this into a saved skill describing this funnel model for future questions on this service, or (b) drill further into the Android docu→pay drop or the payment-failure step with payment_method/error-level data if available?

## Probe 3

Prompt:

> Are there any regressions or trends over the last quarter?

Expected output:

Thoughts
Copy thoughts to clipboard


Used 2 tools
— ClickHouse

Thoughts
Copy thoughts to clipboard

Ran run_select_query

Ran run_select_query
in ClickHouse



Thoughts
Copy thoughts to clipboard

Summary: No quarter of history exists — here's what the available data shows
Key finding: atlys-context-compiler was created on 2026-08-01, and system.query_log only contains ∼22 hours of history (2026-08-01 08:25 → 2026-08-02 06:53, ∼91.5K query records). There is no last-quarter (or even last-month) baseline to compare against, so a true quarter-over-quarter regression analysis isn't possible yet. Below is what the available ∼1‑day window shows.

1. Hourly latency/error trend (full available window)
Query run (capped/bounded per safety rules):

sql
SELECT toStartOfHour(event_time) AS hour, count() AS queries,
       countIf(type='ExceptionWhileProcessing') AS errors,
       round(avg(query_duration_ms)) AS avg_ms, round(quantile(0.95)(query_duration_ms)) AS p95_ms,
       round(avg(memory_usage)/1e6,1) AS avg_mem_mb
FROM system.query_log
WHERE type IN ('QueryFinish','ExceptionWhileProcessing')
GROUP BY hour ORDER BY hour
SETTINGS max_execution_time=30, max_rows_to_read=1000000000, max_bytes_to_read=100000000000, timeout_before_checking_execution_speed=0
Error rate: 0% every hour, no exceptions at all in the whole window.
Latency is flat and healthy: avg 13–26 ms/query, p95 36–90 ms — no upward drift, no regression.
avg memory per query crept from ∼4.5 MB → ∼8.0 MB hour-over-hour — a mild rise, but the absolute values are trivially small and consistent with metrics/log tables growing as the service warms up. Not a concerning trend.
2. Who's generating the load
user	calls	avg ms	total sec	notes
monitoring-internal	56,363	11.9	672	ClickHouse Cloud control plane
observability-internal	20,275	22.5	455	ClickHouse Cloud control plane
operator-internal	1,299	111.4	145	ClickHouse Cloud control plane
default	3,630	32.6	118	your app — 488 inserts, 1,919 selects
prometheus-internal / management-internal / availability-monitoring-internal	∼8.4K combined	low	∼50	ClickHouse Cloud control plane
sql-console (uditrawal209@gmail.com)	∼1,542	3–14	∼13	manual SQL console session
∼93% of all query volume is ClickHouse Cloud's own internal monitoring/control-plane traffic (system.parts/metric_log scraping, log flushing, etc.), not application queries. That's expected overhead, not a symptom of anything wrong — but worth knowing when interpreting "query trends" on this service.

3. Notable single event (not a trend, a one-off)
INSERT INTO destination_card_clicked FORMAT Parquet at 2026-08-01 08:45 took 24.8s and 1.2GB memory — read 1M rows / 452MB. This is almost certainly the initial bulk data load right after the service/tables were created. It's the single most expensive query in the whole window, but it happened once, so there's no "before" state to regress from.

Bottom line
No regressions detected in the available data — latency and error rate are flat/zero across the full ∼22-hour history.
No quarter-scale trend can be computed — the service is ∼1 day old. Once several weeks of history accumulate, this same hourly/daily breakdown (and week-over-week comparison of avg_ms/p95_ms/error rate per normalized query) will give a meaningful trend view.
If you want, I can set up the exact query pattern to re-run periodically, or check the other service (My first service-ch-atlys, idle, same creation date) for comparison.
Source: raw exploration of system.query_log on atlys-context-compiler · Freshness: data through 2026-08-02 06:53 (max event_time in table; service created 2026-08-01) · Key filters: type IN ('QueryFinish','ExceptionWhileProcessing'), no user/bot exclusions applied · Confidence: high for "no regression in available window," but low/not applicable for any quarter-level trend claim since no such history exists.

## Probe 4

Prompt:

> Is anything in the base context wrong, stale, or self-contradictory?

Expected output:

I found the actual object you're asking about: compiler_meta.context_sources stores base_context.md (the literal "base context" doc, 7.7KB, hand-maintained per its own header) plus two newer companion docs (ANALYTICS_KNOWLEDGE_BASE.md, FEATURE_PATTERN_REFERENCE.md). I read the full content and cross-checked its specific, falsifiable claims against the live event tables in clickathon1/clickathon2, plus the compiler's own context_issues/context_versions audit trail. Here's what's actually wrong, stale, or self-contradictory — each backed by a query, not just re-stating the doc.

Confirmed problems
#	Type	Finding	Evidence
1	Wrong + self-contradictory	§2 claims application_started carries visa_issuance_eta_days ("an integer number of days"). §3's own table for the same event lists the actual column as eta_shown — a different name. Live data shows the real column eta_shown holds categorical strings ("3-5 days", "24 hours", "5-7 days", "7-10 days"), not an integer day count, and visa_issuance_eta_days doesn't exist anywhere in the schema.	SELECT eta_shown, count() FROM clickathon2.application_started GROUP BY eta_shown → 4 string buckets, no integer field. Also flagged internally as open issue CTX-001 ("ETA field contradiction").
2	Self-contradictory	§4 defines "Conversion rate" two incompatible ways in the same document: "completed purchases ÷ sessions" (the headline number for leadership) vs. the boxed note just below it: conversion = purchase_completed ÷ application_started ("the denominator used in the drop-off dashboards"). Both are called "conversion rate" with no qualifying name.	Matches open issue CTX-002 ("Conversion denominator ambiguity") — the doc itself is the source of the ambiguity, not just downstream usage.
3	Wrong (stale relative to real data)	§1 states Atlys operates "across 120+ destinations." The live data has only 27 distinct destinations, consistently, in both application_started and destination_card_clicked across clickathon1 and clickathon2.	SELECT uniqExact(destination) FROM clickathon2.application_started → 27; same in destination_card_clicked and in clickathon1. (Could be a demo/sample dataset rather than full production scale — flagging as a discrepancy either way, since anyone using this doc to reason about destination coverage will be misled.)
4	Stale (missing coverage)	§3's "eight raw event tables" is presented as the complete event inventory. clickathon2 matches it exactly. But clickathon1 now has 6 additional feature event tables not mentioned anywhere in base_context.md: abandoned_checkout_recovery_events, express_checkout_events, group_family_applications_events, instant_forex_add_on_events, promo_coupon_at_checkout_sealed_6th_spec_events, recipient_status_sharing_events, visa_status_sharing_events.	system.tables for clickathon1; corroborated by compiler_meta.context_changelog, which recorded these as schema_added between 2026-08-01 22:08 and 2026-08-02 06:30 (versions 8→85) — all after base_context.md was last ingested. The doc even warns about this itself: "it is maintained by hand and can lag the data." This is that lag, concretely.
5	Wrong mapping (compiled layer, not the doc itself)	The compiler's derived context_entities table defines a session entity with key field session_id. No table in either database has a session_id column — the real column is app_session_id. base_context.md §6 (join map) doesn't define a session join key at all, so the compiler appears to have invented one.	system.columns / table DDLs show app_session_id everywhere; context_entities row for session says key_fields_json = ["session_id"].
6	Governance staleness (pipeline, not prose)	The compiled semantic layer — context_entities, context_metrics, context_relationships, and all 34 context_issues (all still status = 'open', none resolved) — was last computed at context version 7 (2026‑08‑01 22:08). The context has since advanced to version 85 (2026‑08‑02 06:30) with 78 more schema changes, including the 6 new tables in #4. None of those newer tables/features have ever been entity-mapped, metric-defined, or issue-checked.	GROUP BY context_version_id on all four tables returns only versions 1 and 7, out of 85 total versions.
7	Possible false-staleness signal in the compiler itself	context_changelog shows version 78 (06:00:37) recording "promo_coupon_at_checkout_sealed_6th_spec_events columns no longer observed" for ∼20 columns, then version 79 two minutes later (06:02:52) recording those exact same columns as newly "added" again. That's schema flapping on a single table within 2 minutes — likely a sampling/detection instability in the compiler's schema-diffing, not a real schema change.	compiler_meta.context_changelog, change_ids 061a40ce… (v78) and cba5ed1d… (v79).
What checked out fine (for balance)
user_id really is a fixed 28-character string, as claimed (min=max=28 across 154k rows).
The "legacy tables sorted ORDER BY (id, timestamp, user_id)" quirk is accurately documented and confirmed in the live DDL for all 8 original tables.
Revenue/currency columns (value, currency, insurance_amount, coupon_applied) and the multi-currency reality (9 distinct currencies in purchase_completed) are accurately reflected — base_context.md correctly caveats revenue as currency-denominated, and CTX-004 correctly flags the unresolved FX policy.
Interpretation
Items 1–3 are genuine content defects in base_context.md itself (wrong facts / internal contradiction). Item 4 is the document failing to keep pace with a rapidly evolving warehouse (new feature verticals in clickathon1). Items 5–7 are defects in the compiled context layer sitting on top of the document — it hasn't been re-run against 78 versions' worth of schema change, and its own change-detection may be noisy.

Recommendation
Not my call to fix the doc, but the compiler's context_issues table is designed exactly for this and already tracks 1–3 as CTX-001/CTX-002 (open) — worth re-running the issue-detection pass against the current version (85) rather than the stale version 7 snapshot, and adding entity/metric/relationship coverage for the 6 new feature tables.

Source: raw exploration of compiler_meta.* (context governance tables) cross-checked against live tables in clickathon1/clickathon2 on service atlys-context-compiler · Freshness: base_context.md last ingested/re-attached 2026‑08‑02 06:30 (context version 85), but its own derived issues/entities/metrics date to version 7 (2026‑08‑01 22:08); live event data spans through the same window · Key filters: none (full table scans on small governance tables; targeted column checks on event tables) · Confidence: high for items 1–5 (directly verified against query output), medium for item 6 (inferred from changelog cadence, plausible alternate explanation is a debounce/sampling artifact)










## Output storage

Suggested storage location:

- [probe_outputs/probe_01.md](probe_outputs/probe_01.md)
- [probe_outputs/probe_02.md](probe_outputs/probe_02.md)
- [probe_outputs/probe_03.md](probe_outputs/probe_03.md)
- [probe_outputs/probe_04.md](probe_outputs/probe_04.md)
