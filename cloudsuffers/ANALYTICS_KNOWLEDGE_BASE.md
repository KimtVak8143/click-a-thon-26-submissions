# Product Analytics Knowledge Base
## Field Guide for Event Analysis, DDL Review & Data Quality

**Version:** 1.0  
**Last Updated:** 2026-08-02  
**Purpose:** Reusable framework for analyzing product features, event instrumentation, and deriving actionable insights

---

## Table of Contents
1. [Event Schema Analysis](#1-event-schema-analysis)
2. [Feature Pattern Classification](#2-feature-pattern-classification)
3. [Data Quality Checklist](#3-data-quality-checklist)
4. [Funnel Analysis Framework](#4-funnel-analysis-framework)
5. [Anomaly Detection Patterns](#5-anomaly-detection-patterns)
6. [DDL Review Guidelines](#6-ddl-review-guidelines)
7. [Metrics Derivation Playbook](#7-metrics-derivation-playbook)
8. [Segmentation Strategies](#8-segmentation-strategies)
9. [Cross-Feature Analysis](#9-cross-feature-analysis)
10. [SQL Query Patterns](#10-sql-query-patterns)

---

## 1. Event Schema Analysis

### 1.1 Standard Envelope Pattern
Every product event should contain these core dimensions:

```
Common Envelope Fields:
├── Identification
│   ├── event (string) - event name
│   ├── id (string) - unique event ID
│   ├── timestamp (datetime) - when event occurred
│   ├── user_id (string) - user identifier
│   └── application_id (string) - transaction/session identifier
├── Technical Context
│   ├── device_type (string) - device category
│   ├── os (string) - operating system
│   ├── app_version (string) - application version
│   └── client_lib (string) - SDK/library used
└── Geographic Context
    ├── geoip_country_code (string) - country
    └── city (string) - city (optional)
```

**Quality Checks:**
- [ ] All events have consistent envelope structure
- [ ] No duplicate field names with different meanings
- [ ] Timestamp format is consistent (ISO 8601 preferred)
- [ ] IDs are properly formatted (UUIDs, hashes, etc.)

### 1.2 Event Naming Conventions

**Good Patterns:**
```
✓ object_action_past_tense
  - express_checkout_shown
  - payment_confirmed
  - traveller_added

✓ Descriptive and specific
✓ Past tense indicates completion
✓ Hierarchical naming for related events
```

**Anti-Patterns:**
```
✗ Vague: button_clicked (which button?)
✗ Present tense: checkout_show
✗ Inconsistent: checkout_start vs begin_payment
✗ Too generic: event, action, track
```

### 1.3 Nested Objects vs Flat Fields

**When to use nested objects:**
- Logically grouped data (e.g., `payment: {amount, currency, latency_ms}`)
- Optional complex data structures
- Multiple related attributes that always travel together

**When to flatten:**
- Simple key dimensions needed for filtering
- Fields used in WHERE clauses frequently
- Data that needs column-level indexing

---

## 2. Feature Pattern Classification

### 2.1 Pattern Types

| Pattern | Characteristics | Event Flow | Key Metrics |
|---------|----------------|------------|-------------|
| **Linear Funnel** | Sequential steps, clear progression | A → B → C → D | Conversion rate, drop-off by step |
| **Iterative Loop** | Repeated actions, state changes | Start → (Add/Edit/Remove)* → Submit | Actions per session, completion rate |
| **Viral Loop** | Multi-user, cross-entity tracking | Share → Open → Action | K-factor, viral coefficient |
| **Recovery Flow** | Triggered by inactivity/failure | Drop → Detect → Nudge → Return | Recovery rate, time-to-return |
| **Upsell/Cross-sell** | Optional add-on to main flow | Offer → Engage → Purchase | Attach rate, AOV lift |
| **Multi-path** | Multiple routes to same goal | (Path A or Path B or Path C) → Goal | Path preference, conversion by path |

### 2.2 Pattern-Specific Analysis Questions

#### Linear Funnel Features
```sql
-- Critical Questions:
- What's the overall conversion rate (first step → last step)?
- Where is the biggest drop-off?
- Are there platform/segment differences?
- What's the median time-to-convert?

-- Watch for:
- Steps shown but never actioned (dead-ends)
- Steps with >50% drop-off (friction points)
- Very fast progressions (potential bot/test traffic)
```

#### Iterative Loop Features
```sql
-- Critical Questions:
- What's the distribution of iterations before completion?
- Is there add/remove churn indicating UX issues?
- Do incomplete states block submission?
- What's the abandonment rate mid-loop?

-- Watch for:
- High remove-to-add ratios (users making mistakes)
- Incomplete loops never submitted
- Index/ordering issues in iteration tracking
```

#### Viral Loop Features
```sql
-- Critical Questions:
- What % of users share?
- What's the open rate by channel?
- What's the new user conversion rate?
- Which content types/statuses drive sharing?

-- Watch for:
- Shares generated but never opened (broken links?)
- Same share_id opened multiple times (tracking issues)
- Channel attribution gaps
```

---

## 3. Data Quality Checklist

### 3.1 Structural Integrity

**Event Completeness:**
```python
# Check for each event stream:
□ All expected events are present
□ Event sequence is logical (no orphaned events)
□ No duplicate events (same ID, timestamp, user)
□ Proper foreign key relationships (user_id, application_id)
```

**Field Validation:**
```python
# For each field:
□ Data type matches schema definition
□ Null/empty rate is within acceptable threshold
□ Enum values are within expected set
□ Numeric ranges are valid (no negative prices)
□ Date ranges are realistic (no future dates)
```

### 3.2 Common Data Quality Issues

| Issue | Detection | Impact | Resolution |
|-------|-----------|--------|------------|
| **Null OS values** | `WHERE os IS NULL` | Breaks device segmentation | Device type fallback, SDK update |
| **Inconsistent device_type** | Multiple naming conventions | Fragmented reporting | Normalization layer, enum enforcement |
| **Duplicate events** | Same event_id appears 2x | Inflated metrics | Deduplication in ETL, unique constraints |
| **Missing event steps** | Funnel has gaps | Incomplete analysis | SDK audit, event validation |
| **Timezone inconsistencies** | Timestamps in mixed zones | Time series errors | UTC standardization |
| **Schema drift** | New fields appear unexpectedly | Query breaks | Schema versioning, validation |

### 3.3 Data Quality SQL Checks

```sql
-- Template: Event Distribution Health Check
SELECT 
    event,
    COUNT(*) as event_count,
    COUNT(DISTINCT user_id) as unique_users,
    COUNT(DISTINCT DATE(timestamp)) as days_with_events,
    MIN(timestamp) as first_seen,
    MAX(timestamp) as last_seen,
    COUNT(*) / COUNT(DISTINCT user_id) as events_per_user
FROM events
GROUP BY event
ORDER BY event_count DESC;

-- Template: Null Value Audit
SELECT 
    event,
    SUM(CASE WHEN device_type IS NULL THEN 1 ELSE 0 END) as null_device,
    SUM(CASE WHEN os IS NULL THEN 1 ELSE 0 END) as null_os,
    SUM(CASE WHEN geoip_country_code IS NULL THEN 1 ELSE 0 END) as null_country,
    COUNT(*) as total_events,
    ROUND(100.0 * SUM(CASE WHEN os IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) as pct_null_os
FROM events
GROUP BY event
HAVING pct_null_os > 5;  -- Flag if >5% null

-- Template: Event Sequence Validation
WITH event_sequences AS (
    SELECT 
        user_id,
        application_id,
        event,
        timestamp,
        LAG(event) OVER (PARTITION BY user_id, application_id ORDER BY timestamp) as prev_event
    FROM events
)
SELECT 
    prev_event || ' → ' || event as event_pair,
    COUNT(*) as occurrences
FROM event_sequences
WHERE prev_event IS NOT NULL
GROUP BY prev_event, event
ORDER BY occurrences DESC;
```

---

## 4. Funnel Analysis Framework

### 4.1 Standard Funnel Metrics

```sql
-- Core Funnel Template
WITH funnel_steps AS (
    SELECT 
        user_id,
        application_id,
        MAX(CASE WHEN event = 'step_1_shown' THEN 1 ELSE 0 END) as reached_step_1,
        MAX(CASE WHEN event = 'step_2_started' THEN 1 ELSE 0 END) as reached_step_2,
        MAX(CASE WHEN event = 'step_3_completed' THEN 1 ELSE 0 END) as reached_step_3,
        MAX(CASE WHEN event = 'step_4_confirmed' THEN 1 ELSE 0 END) as reached_step_4
    FROM events
    WHERE timestamp >= '2026-06-01'
    GROUP BY user_id, application_id
)
SELECT 
    SUM(reached_step_1) as step_1_users,
    SUM(reached_step_2) as step_2_users,
    SUM(reached_step_3) as step_3_users,
    SUM(reached_step_4) as step_4_users,
    ROUND(100.0 * SUM(reached_step_2) / NULLIF(SUM(reached_step_1), 0), 2) as step1_to_2_pct,
    ROUND(100.0 * SUM(reached_step_3) / NULLIF(SUM(reached_step_2), 0), 2) as step2_to_3_pct,
    ROUND(100.0 * SUM(reached_step_4) / NULLIF(SUM(reached_step_3), 0), 2) as step3_to_4_pct,
    ROUND(100.0 * SUM(reached_step_4) / NULLIF(SUM(reached_step_1), 0), 2) as overall_conversion
FROM funnel_steps;
```

### 4.2 Time-to-Convert Analysis

```sql
-- Template: Time Between Funnel Steps
WITH step_timestamps AS (
    SELECT 
        user_id,
        application_id,
        MIN(CASE WHEN event = 'funnel_start' THEN timestamp END) as start_time,
        MIN(CASE WHEN event = 'funnel_end' THEN timestamp END) as end_time
    FROM events
    GROUP BY user_id, application_id
    HAVING start_time IS NOT NULL AND end_time IS NOT NULL
)
SELECT 
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (end_time - start_time))) as median_seconds,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (end_time - start_time))) as p75_seconds,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (end_time - start_time))) as p95_seconds,
    AVG(EXTRACT(EPOCH FROM (end_time - start_time))) as avg_seconds
FROM step_timestamps;
```

### 4.3 Segmented Funnel Analysis

```sql
-- Template: Funnel by Segment
SELECT 
    segment_dimension,  -- e.g., device_type, geoip_country_code, destination
    COUNT(DISTINCT CASE WHEN event = 'step_1' THEN user_id END) as step_1_users,
    COUNT(DISTINCT CASE WHEN event = 'step_2' THEN user_id END) as step_2_users,
    COUNT(DISTINCT CASE WHEN event = 'step_3' THEN user_id END) as step_3_users,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event = 'step_3' THEN user_id END) / 
          NULLIF(COUNT(DISTINCT CASE WHEN event = 'step_1' THEN user_id END), 0), 2) as conversion_rate
FROM events
GROUP BY segment_dimension
ORDER BY step_1_users DESC;
```

---

## 5. Anomaly Detection Patterns

### 5.1 Statistical Anomalies

**Volume Anomalies:**
```sql
-- Template: Detect Daily Volume Spikes/Drops
WITH daily_volumes AS (
    SELECT 
        DATE(timestamp) as event_date,
        event,
        COUNT(*) as event_count
    FROM events
    GROUP BY DATE(timestamp), event
),
stats AS (
    SELECT 
        event,
        AVG(event_count) as avg_count,
        STDDEV(event_count) as stddev_count
    FROM daily_volumes
    GROUP BY event
)
SELECT 
    dv.event_date,
    dv.event,
    dv.event_count,
    s.avg_count,
    ROUND((dv.event_count - s.avg_count) / NULLIF(s.stddev_count, 0), 2) as z_score,
    CASE 
        WHEN ABS((dv.event_count - s.avg_count) / NULLIF(s.stddev_count, 0)) > 3 THEN '⚠️ ANOMALY'
        ELSE 'Normal'
    END as status
FROM daily_volumes dv
JOIN stats s ON dv.event = s.event
ORDER BY ABS(z_score) DESC;
```

### 5.2 Behavioral Anomalies

**Red Flags to Watch:**
```
🚩 Conversion Rate Changes:
   - Sudden drops >10% week-over-week
   - Platform-specific degradation
   - Geo/segment divergence

🚩 Time-Based Anomalies:
   - Actions completing in <1 second (bot behavior)
   - Unusually long times between steps (stuck users)
   - Timestamp ordering violations

🚩 Data Distribution Shifts:
   - New categorical values appearing
   - Ratio changes (mobile vs desktop)
   - Unexpected null rate increases

🚩 Event Sequence Anomalies:
   - End events without start events
   - Missing intermediate steps
   - Duplicate critical events (e.g., payment_confirmed 2x)
```

### 5.3 Schema Evolution Detection

```sql
-- Template: Detect New Fields or Schema Changes
SELECT 
    DATE(timestamp) as event_date,
    event,
    jsonb_object_keys(event_properties) as property_key,  -- For JSONB columns
    COUNT(*) as appearances
FROM events
GROUP BY DATE(timestamp), event, property_key
HAVING MIN(DATE(timestamp)) > CURRENT_DATE - INTERVAL '7 days'
ORDER BY event_date DESC;
```

---

## 6. DDL Review Guidelines

### 6.1 Table Design Checklist

**Fact Table Design:**
```sql
-- ✓ Good Event Fact Table
CREATE TABLE events (
    event_id VARCHAR(64) PRIMARY KEY,           -- Unique event identifier
    event_name VARCHAR(255) NOT NULL,           -- Event type
    timestamp TIMESTAMP NOT NULL,               -- When it happened
    user_id VARCHAR(64) NOT NULL,               -- Who
    application_id VARCHAR(64),                 -- Transaction context
    
    -- Dimensional attributes
    device_type VARCHAR(50),
    os VARCHAR(50),
    app_version VARCHAR(20),
    geoip_country_code CHAR(2),
    city VARCHAR(100),
    
    -- Event-specific properties (consider JSONB for flexibility)
    event_properties JSONB,
    
    -- Metadata
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Indexes for common queries
    INDEX idx_timestamp (timestamp),
    INDEX idx_user_event (user_id, event_name, timestamp),
    INDEX idx_application (application_id, timestamp)
);

-- ✓ Good Dimension Table
CREATE TABLE dim_destinations (
    destination_code CHAR(2) PRIMARY KEY,
    destination_name VARCHAR(100) NOT NULL,
    region VARCHAR(50),
    continent VARCHAR(50),
    is_schengen BOOLEAN,
    avg_processing_days INT
);
```

**Anti-Patterns:**
```sql
-- ✗ Avoid: Wide tables with too many nullable columns
CREATE TABLE bad_events (
    id INT,
    event VARCHAR(50),
    timestamp TIMESTAMP,
    -- 50+ optional columns, mostly null
    forex_amount DECIMAL(10,2),  -- Only relevant for forex events
    group_size INT,              -- Only for group events
    share_channel VARCHAR(20),   -- Only for share events
    ...
);

-- ✗ Avoid: Storing computed metrics in raw tables
CREATE TABLE events_with_metrics (
    ...
    conversion_rate DECIMAL(5,2),  -- Derived metric, not raw data
    ...
);
```

### 6.2 Partitioning Strategy

```sql
-- Best Practice: Partition by date for time-series data
CREATE TABLE events (
    ...
) PARTITION BY RANGE (timestamp);

CREATE TABLE events_2026_06 PARTITION OF events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE events_2026_07 PARTITION OF events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
```

### 6.3 Index Strategy

```sql
-- Priority Indexing:
1. Timestamp (nearly all queries filter by date)
2. User ID (user journey analysis)
3. Event name + timestamp (event-specific queries)
4. Application ID (transaction tracking)
5. Composite indexes for common query patterns

-- Example Composite Index:
CREATE INDEX idx_user_journey 
ON events(user_id, timestamp, event_name)
INCLUDE (device_type, geoip_country_code);
```

---

## 7. Metrics Derivation Playbook

### 7.1 Core Metric Categories

#### Conversion Metrics
```sql
-- Conversion Rate = Converters / Total Eligible Users
SELECT 
    COUNT(DISTINCT CASE WHEN reached_end THEN user_id END) as converters,
    COUNT(DISTINCT user_id) as total_users,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN reached_end THEN user_id END) / 
          COUNT(DISTINCT user_id), 2) as conversion_rate_pct
FROM user_funnels;

-- Incremental Conversion (Step-by-Step)
-- Shows conversion from each step to next step
```

#### Engagement Metrics
```sql
-- Actions Per User
SELECT 
    user_id,
    COUNT(*) as total_actions,
    COUNT(DISTINCT DATE(timestamp)) as active_days,
    COUNT(*) / NULLIF(COUNT(DISTINCT DATE(timestamp)), 0) as actions_per_day
FROM events
GROUP BY user_id;

-- Feature Adoption Rate
SELECT 
    COUNT(DISTINCT CASE WHEN used_feature THEN user_id END) as adopters,
    COUNT(DISTINCT user_id) as total_users,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN used_feature THEN user_id END) / 
          COUNT(DISTINCT user_id), 2) as adoption_rate_pct
FROM feature_usage;
```

#### Revenue Metrics
```sql
-- Average Order Value (AOV)
SELECT 
    AVG(order_value) as avg_order_value,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY order_value) as median_order_value
FROM orders
WHERE status = 'completed';

-- Attach Rate (for upsells)
SELECT 
    COUNT(DISTINCT CASE WHEN addon_purchased THEN order_id END) as orders_with_addon,
    COUNT(DISTINCT order_id) as total_orders,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN addon_purchased THEN order_id END) / 
          COUNT(DISTINCT order_id), 2) as attach_rate_pct
FROM orders;

-- AOV Lift from Add-on
SELECT 
    AVG(CASE WHEN addon_purchased THEN order_value END) as avg_with_addon,
    AVG(CASE WHEN NOT addon_purchased THEN order_value END) as avg_without_addon,
    AVG(CASE WHEN addon_purchased THEN order_value END) - 
    AVG(CASE WHEN NOT addon_purchased THEN order_value END) as aov_lift
FROM orders;
```

#### Retention & Recovery Metrics
```sql
-- Recovery Rate
SELECT 
    COUNT(DISTINCT CASE WHEN recovered THEN user_id END) as recovered_users,
    COUNT(DISTINCT user_id) as abandoned_users,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN recovered THEN user_id END) / 
          COUNT(DISTINCT user_id), 2) as recovery_rate_pct
FROM abandoned_checkouts;
```

#### Viral & Growth Metrics
```sql
-- K-Factor (Viral Coefficient)
SELECT 
    COUNT(DISTINCT share_id) as shares_generated,
    COUNT(DISTINCT CASE WHEN opened THEN share_id END) as shares_opened,
    COUNT(DISTINCT CASE WHEN converted THEN recipient_user_id END) as new_users_acquired,
    ROUND(1.0 * COUNT(DISTINCT CASE WHEN converted THEN recipient_user_id END) / 
          NULLIF(COUNT(DISTINCT sharer_user_id), 0), 2) as k_factor
FROM viral_shares;
```

### 7.2 Metric Quality Standards

**Good Metric Characteristics:**
- [ ] **Actionable**: Teams can influence it
- [ ] **Accessible**: Easy to understand and compute
- [ ] **Auditable**: Can be validated and reconciled
- [ ] **Timely**: Available soon after events occur
- [ ] **Comparable**: Consistent definition over time
- [ ] **Segmentable**: Can be broken down by dimensions

---

## 8. Segmentation Strategies

### 8.1 Standard Segmentation Dimensions

| Dimension | Use Case | Example Segments |
|-----------|----------|------------------|
| **Device** | Platform performance, UX optimization | iOS, Android, Web Desktop, Web Mobile |
| **Geography** | Localization, market analysis | Country, City, Region, Timezone |
| **Destination** | Product offering optimization | High-volume vs low-volume countries |
| **User Type** | Personalization, targeting | New vs Returning, Free vs Paid |
| **Time-based** | Seasonality, trends | Day of week, Hour of day, Month |
| **Behavior** | Engagement tiers | Power users, Casual users, At-risk |
| **Acquisition** | Channel effectiveness | Organic, Paid, Referral, Viral |

### 8.2 Cohort Analysis Template

```sql
-- Template: Weekly Cohort Conversion
WITH user_cohorts AS (
    SELECT 
        user_id,
        DATE_TRUNC('week', MIN(timestamp)) as cohort_week,
        MIN(timestamp) as first_seen
    FROM events
    GROUP BY user_id
),
cohort_conversions AS (
    SELECT 
        uc.cohort_week,
        COUNT(DISTINCT uc.user_id) as cohort_size,
        COUNT(DISTINCT CASE WHEN e.event = 'converted' THEN uc.user_id END) as converters
    FROM user_cohorts uc
    LEFT JOIN events e ON uc.user_id = e.user_id
    GROUP BY uc.cohort_week
)
SELECT 
    cohort_week,
    cohort_size,
    converters,
    ROUND(100.0 * converters / cohort_size, 2) as conversion_rate_pct
FROM cohort_conversions
ORDER BY cohort_week DESC;
```

### 8.3 RFM Segmentation for Re-engagement

```sql
-- Recency, Frequency, Monetary
WITH user_rfm AS (
    SELECT 
        user_id,
        EXTRACT(DAYS FROM CURRENT_DATE - MAX(timestamp)) as recency_days,
        COUNT(*) as frequency,
        SUM(revenue) as monetary_value
    FROM user_activity
    GROUP BY user_id
),
rfm_scores AS (
    SELECT 
        user_id,
        NTILE(5) OVER (ORDER BY recency_days DESC) as r_score,  -- Recent is better (lower days)
        NTILE(5) OVER (ORDER BY frequency) as f_score,
        NTILE(5) OVER (ORDER BY monetary_value) as m_score
    FROM user_rfm
)
SELECT 
    user_id,
    CASE 
        WHEN r_score >= 4 AND f_score >= 4 THEN 'Champions'
        WHEN r_score >= 3 AND f_score >= 3 THEN 'Loyal Customers'
        WHEN r_score >= 4 AND f_score <= 2 THEN 'Promising'
        WHEN r_score <= 2 THEN 'At Risk'
        ELSE 'Regular'
    END as segment
FROM rfm_scores;
```

---

## 9. Cross-Feature Analysis

### 9.1 Feature Interaction Matrix

```sql
-- Template: Which features are used together?
WITH feature_usage AS (
    SELECT 
        user_id,
        MAX(CASE WHEN event LIKE 'express_checkout%' THEN 1 ELSE 0 END) as used_express,
        MAX(CASE WHEN event LIKE 'group_%' THEN 1 ELSE 0 END) as used_group,
        MAX(CASE WHEN event LIKE 'share_%' THEN 1 ELSE 0 END) as used_share,
        MAX(CASE WHEN event LIKE 'forex_%' THEN 1 ELSE 0 END) as used_forex
    FROM events
    GROUP BY user_id
)
SELECT 
    SUM(used_express) as express_users,
    SUM(used_group) as group_users,
    SUM(used_express * used_group) as express_and_group,
    SUM(used_express * used_forex) as express_and_forex,
    ROUND(100.0 * SUM(used_express * used_group) / NULLIF(SUM(used_express), 0), 2) as pct_express_also_group
FROM feature_usage;
```

### 9.2 Feature Sequence Analysis

```sql
-- What features do users engage with first?
WITH first_feature_touch AS (
    SELECT 
        user_id,
        FIRST_VALUE(feature_category) OVER (
            PARTITION BY user_id 
            ORDER BY timestamp
        ) as entry_feature,
        feature_category as any_feature,
        timestamp
    FROM events
    WHERE feature_category IS NOT NULL
)
SELECT 
    entry_feature,
    any_feature as subsequent_feature,
    COUNT(*) as users_with_sequence
FROM first_feature_touch
WHERE entry_feature != any_feature
GROUP BY entry_feature, any_feature
ORDER BY users_with_sequence DESC;
```

---

## 10. SQL Query Patterns

### 10.1 Common Query Templates

#### User Journey Reconstruction
```sql
-- Template: Full user journey for a specific user
SELECT 
    timestamp,
    event,
    device_type,
    event_properties
FROM events
WHERE user_id = 'specific_user_id'
ORDER BY timestamp;
```

#### Event Distribution Over Time
```sql
-- Template: Daily event volumes with 7-day moving average
SELECT 
    DATE(timestamp) as event_date,
    event,
    COUNT(*) as daily_count,
    AVG(COUNT(*)) OVER (
        PARTITION BY event 
        ORDER BY DATE(timestamp) 
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) as moving_avg_7day
FROM events
GROUP BY DATE(timestamp), event
ORDER BY event_date, event;
```

#### Conversion Window Analysis
```sql
-- Template: Time between first touch and conversion
WITH user_timeline AS (
    SELECT 
        user_id,
        MIN(CASE WHEN event = 'first_touch' THEN timestamp END) as first_touch,
        MIN(CASE WHEN event = 'converted' THEN timestamp END) as conversion
    FROM events
    GROUP BY user_id
    HAVING MIN(CASE WHEN event = 'converted' THEN timestamp END) IS NOT NULL
)
SELECT 
    EXTRACT(HOUR FROM (conversion - first_touch)) as hours_to_convert,
    COUNT(*) as user_count
FROM user_timeline
GROUP BY EXTRACT(HOUR FROM (conversion - first_touch))
ORDER BY hours_to_convert;
```

### 10.2 Performance Optimization Tips

```sql
-- ✓ Do: Filter on indexed columns first
SELECT * FROM events
WHERE timestamp >= '2026-06-01'  -- Indexed, evaluated first
  AND event = 'specific_event'   -- Then filter
LIMIT 1000;

-- ✓ Do: Use EXISTS for existence checks
SELECT user_id FROM users u
WHERE EXISTS (
    SELECT 1 FROM orders o 
    WHERE o.user_id = u.user_id 
    AND o.status = 'completed'
);

-- ✗ Avoid: SELECT * when you need specific columns
-- ✗ Avoid: DISTINCT without understanding cardinality
-- ✗ Avoid: Functions on indexed columns in WHERE (breaks index usage)
```

---

## 11. Reporting & Dashboard Design

### 11.1 Dashboard Hierarchy

```
Executive Dashboard (Daily Review)
├── North Star Metrics
│   ├── Total Conversions
│   ├── Conversion Rate
│   └── Revenue
├── Key Feature Metrics
│   ├── Feature Adoption %
│   ├── Feature Conversion Rates
│   └── Feature Revenue Contribution
└── Health Indicators
    ├── Data Quality Score
    ├── Error Rates
    └── System Availability

Feature-Specific Dashboard (Product Team)
├── Funnel Visualization
├── Segment Breakdowns
├── Time-Series Trends
└── Cohort Performance

Technical Dashboard (Engineering/Data)
├── Event Volume Trends
├── Schema Validation Status
├── Query Performance
└── Data Freshness
```

### 11.2 Alert Configuration

**Critical Alerts (Immediate Action):**
- Conversion rate drops >20% vs previous day
- Event volume drops >50% vs previous hour
- Error rate exceeds 5%
- Data pipeline failures

**Warning Alerts (Review within 24h):**
- Conversion rate drops 10-20%
- New fields detected in event schema
- Segment performance divergence >15%
- Unusual geographic distribution

---

## 12. Analysis Workflow Checklist

### 12.1 New Feature Analysis Process

```markdown
Phase 1: Discovery (Day 1)
□ Review feature spec and expected events
□ Validate events are being received
□ Check data quality (null rates, schema)
□ Identify key metrics to track

Phase 2: Baseline Establishment (Week 1)
□ Calculate baseline conversion rates
□ Establish segment benchmarks
□ Set up monitoring dashboards
□ Configure alerts

Phase 3: Deep Dive Analysis (Week 2-4)
□ Funnel analysis with drop-off identification
□ Segment performance comparison
□ Time-to-convert analysis
□ Cross-feature interaction analysis

Phase 4: Optimization Recommendations (Ongoing)
□ Identify friction points
□ Propose A/B test hypotheses
□ Calculate potential impact
□ Track implementation results
```

### 12.2 Anomaly Investigation Protocol

```markdown
When an anomaly is detected:
1. Verify it's real (not data quality issue)
2. Determine scope (all users or specific segment?)
3. Check for external factors (holidays, outages, marketing)
4. Review recent code deployments
5. Analyze user feedback/support tickets
6. Compare to historical patterns
7. Document findings and action items
```

---

## 13. Best Practices Summary

### Do's ✓
- Always start with data quality validation
- Use window functions for complex analytics
- Partition large tables by date
- Index on timestamp + frequently filtered columns
- Document metric definitions clearly
- Set up automated quality checks
- Use CTEs for readable complex queries
- Track schema changes over time

### Don'ts ✗
- Don't mix event storage with computed metrics
- Don't create indexes on every column
- Don't use SELECT * in production queries
- Don't compare metrics without understanding definitions
- Don't ignore data quality issues
- Don't hardcode dates in recurring queries
- Don't duplicate logic across multiple queries

---

## 14. Quick Reference: Metric Formulas

```python
# Conversion Metrics
conversion_rate = (converters / total_users) * 100
incremental_conversion = (step_n_users / step_n-1_users) * 100
overall_conversion = (final_step_users / first_step_users) * 100

# Engagement Metrics
adoption_rate = (feature_users / total_users) * 100
actions_per_user = total_actions / unique_users
daily_active_users = COUNT(DISTINCT user_id WHERE date = today)

# Revenue Metrics
average_order_value = total_revenue / order_count
attach_rate = (orders_with_addon / total_orders) * 100
aov_lift = avg_with_addon - avg_without_addon
revenue_per_user = total_revenue / unique_users

# Retention Metrics
retention_rate = (returning_users / cohort_size) * 100
churn_rate = (churned_users / cohort_size) * 100

# Viral Metrics
k_factor = (new_users_from_shares / original_users)
viral_coefficient = invites_sent * conversion_rate_of_invites
share_rate = (users_who_shared / total_users) * 100

# Recovery Metrics
recovery_rate = (recovered_users / abandoned_users) * 100
time_to_recovery = median(recovery_timestamp - abandonment_timestamp)

# Quality Metrics
data_completeness = (non_null_fields / total_expected_fields) * 100
event_success_rate = (successful_events / total_events) * 100
```

---

## 15. Glossary

**Fact Table**: Stores transactional/event data (who, what, when, where, how much)

**Dimension Table**: Stores descriptive attributes (user details, product catalogs)

**Funnel**: Sequential set of steps users take toward a goal

**Cohort**: Group of users sharing a common characteristic (e.g., signup date)

**Segment**: Subset of users defined by attributes (e.g., mobile users, premium users)

**Conversion**: User completing a desired action

**Attach Rate**: % of transactions that include an upsell/add-on

**K-Factor**: Viral growth coefficient (invites sent × invite conversion rate)

**Churn**: Users who stop using the product

**Retention**: Users who continue using the product over time

---

**End of Knowledge Base v1.0**

*This document should be updated as new patterns emerge and lessons are learned from production analytics.*
