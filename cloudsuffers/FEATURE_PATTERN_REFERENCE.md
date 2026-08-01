# Feature Pattern Reference Guide
## Real-World Examples from Atlys Product Features

**Purpose:** Quick reference of actual implementation patterns observed in production features  
**Based on:** Atlys visa application platform feature set  
**Use this for:** Pattern matching when analyzing new features

---

## Pattern Catalog

### Pattern 1: Express Checkout (Linear Funnel + Payment)

**Feature Goal:** Reduce checkout friction for returning users

**Event Flow:**
```
express_checkout_shown 
  ↓ (user decides)
express_checkout_selected 
  ↓ (system loads saved method)
saved_method_used 
  ↓ (OTP verification)
otp_entered 
  ↓ (payment processing)
express_payment_confirmed
```

**Key Attributes to Track:**
- `shown_amount`, `currency` - For cohort analysis by price point
- `saved_method_type` - Card vs UPI vs Wallet performance
- `otp_attempts`, `otp_success` - Friction indicator
- `payment.latency_ms` - User experience metric

**Critical Metrics:**
```sql
-- Shown → Confirmed conversion
SELECT 
    COUNT(DISTINCT CASE WHEN event = 'express_checkout_shown' THEN user_id END) as shown,
    COUNT(DISTINCT CASE WHEN event = 'express_payment_confirmed' THEN user_id END) as confirmed,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event = 'express_payment_confirmed' THEN user_id END) / 
          COUNT(DISTINCT CASE WHEN event = 'express_checkout_shown' THEN user_id END), 2) as conversion_rate
FROM events;

-- OTP failure impact
SELECT 
    device_type,
    os,
    SUM(CASE WHEN otp_success THEN 1 ELSE 0 END) as successful_otps,
    COUNT(*) as total_otp_attempts,
    ROUND(100.0 * SUM(CASE WHEN otp_success THEN 1 ELSE 0 END) / COUNT(*), 2) as otp_success_rate
FROM events
WHERE event = 'otp_entered'
GROUP BY device_type, os
HAVING COUNT(*) > 10
ORDER BY otp_success_rate;
```

**Anomaly Watch:**
- OTP success rate <90% (indicates integration issues)
- Payment latency >10 seconds (poor UX)
- High shown→selected ratio but low selected→confirmed (payment failures)
- Platform-specific conversion gaps >15%

---

### Pattern 2: Group/Family Applications (Iterative Loop)

**Feature Goal:** Enable multi-traveller visa applications

**Event Flow:**
```
group_started (group_id created)
  ↓ (loop: user adds travellers)
traveller_added (with docs_complete flag)
  ↓ (optional: user removes incorrect entries)
traveller_removed
  ↓ (repeat until satisfied)
traveller_added (more travellers)
  ↓ (all ready)
group_submitted (travellers_submitted count)
```

**Key Attributes to Track:**
- `group_id` - Links all related events
- `group_size` - Declared intent
- `traveller_index` - Position in group
- `relation` - Spouse, child, friend, parent, sibling
- `docs_complete` - Per-traveller readiness flag
- `travellers_submitted` - Actual count at submission

**Critical Metrics:**
```sql
-- Completion rate by group size
WITH group_analysis AS (
    SELECT 
        group_id,
        MAX(group_size) as declared_size,
        COUNT(DISTINCT CASE WHEN event = 'traveller_added' THEN traveller_index END) as travellers_added,
        COUNT(DISTINCT CASE WHEN event = 'traveller_removed' THEN traveller_index END) as travellers_removed,
        MAX(CASE WHEN event = 'group_submitted' THEN 1 ELSE 0 END) as submitted
    FROM events
    GROUP BY group_id
)
SELECT 
    declared_size,
    COUNT(*) as groups_started,
    SUM(submitted) as groups_submitted,
    ROUND(100.0 * SUM(submitted) / COUNT(*), 2) as completion_rate,
    AVG(travellers_added) as avg_adds,
    AVG(travellers_removed) as avg_removes
FROM group_analysis
GROUP BY declared_size
ORDER BY declared_size;

-- Document completion blocker analysis
SELECT 
    group_id,
    COUNT(*) as total_travellers,
    SUM(CASE WHEN docs_complete THEN 1 ELSE 0 END) as complete_docs,
    MAX(CASE WHEN event = 'group_submitted' THEN 1 ELSE 0 END) as submitted
FROM events
WHERE event = 'traveller_added'
GROUP BY group_id
HAVING SUM(CASE WHEN NOT docs_complete THEN 1 ELSE 0 END) > 0
   AND MAX(CASE WHEN event = 'group_submitted' THEN 1 ELSE 0 END) = 0;
```

**Anomaly Watch:**
- Add/remove ratio >0.5 (users making mistakes, confusing UX)
- Groups with declared_size ≠ travellers_submitted (tracking issues)
- Large groups (5+) with very low completion rates
- Incomplete docs consistently blocking submission

**Design Lessons:**
- Track both declared intent (group_size) and actual behavior
- Use index/position fields for ordered collections
- Include completion flags to identify blockers
- Link events via group_id for reconstruction

---

### Pattern 3: Visa Status Sharing (Viral Loop)

**Feature Goal:** Drive viral acquisition through status sharing

**Event Flow (Two-Sided):**
```
SHARER SIDE:
share_clicked (status_shared)
  ↓
channel_selected
  ↓
link_generated (share_id created)

RECIPIENT SIDE:
link_opened (share_id, recipient_is_new_user)
  ↓
recipient_cta_clicked
```

**Key Attributes to Track:**
- `share_id` - Links sharer and recipient events
- `status_shared` - submitted, processing, approved
- `channel` - whatsapp, email, sms, copy
- `recipient_is_new_user` - Critical for K-factor
- `cta` - Call-to-action clicked by recipient

**Critical Metrics:**
```sql
-- Overall K-factor calculation
WITH sharers AS (
    SELECT COUNT(DISTINCT user_id) as total_sharers
    FROM events
    WHERE event = 'share_clicked'
),
new_users AS (
    SELECT COUNT(DISTINCT user_id) as new_from_shares
    FROM events
    WHERE event = 'recipient_cta_clicked'
      AND recipient_is_new_user = true
)
SELECT 
    s.total_sharers,
    n.new_from_shares,
    ROUND(1.0 * n.new_from_shares / s.total_sharers, 3) as k_factor
FROM sharers s, new_users n;

-- Channel effectiveness
WITH channel_funnel AS (
    SELECT 
        channel,
        COUNT(DISTINCT CASE WHEN event = 'link_generated' THEN share_id END) as links_generated,
        COUNT(DISTINCT CASE WHEN event = 'link_opened' THEN share_id END) as links_opened,
        COUNT(DISTINCT CASE WHEN event = 'link_opened' AND recipient_is_new_user THEN share_id END) as new_user_opens,
        COUNT(DISTINCT CASE WHEN event = 'recipient_cta_clicked' AND recipient_is_new_user THEN share_id END) as new_user_conversions
    FROM events
    WHERE event IN ('link_generated', 'link_opened', 'recipient_cta_clicked')
    GROUP BY channel
)
SELECT 
    channel,
    links_generated,
    links_opened,
    ROUND(100.0 * links_opened / NULLIF(links_generated, 0), 2) as open_rate,
    ROUND(100.0 * new_user_conversions / NULLIF(new_user_opens, 0), 2) as new_user_conv_rate
FROM channel_funnel
ORDER BY new_user_conversions DESC;

-- Share propensity by status
SELECT 
    status_shared,
    COUNT(DISTINCT user_id) as sharers,
    COUNT(*) as total_shares,
    COUNT(*) / COUNT(DISTINCT user_id) as shares_per_user
FROM events
WHERE event = 'share_clicked'
GROUP BY status_shared
ORDER BY sharers DESC;
```

**Anomaly Watch:**
- Links generated but never opened (broken links, delivery issues)
- Same share_id opened 100+ times (tracking bug or viral hit?)
- Very low open rates on specific channels (<5%)
- Approved status not being shared (missed opportunity)

**Design Lessons:**
- Use unique identifiers to link cross-user events
- Tag recipient events with share_id, not recipient user_id initially
- Track is_new_user flag at recipient side for K-factor
- Include status context in share events for segmentation

---

### Pattern 4: Abandoned Checkout Recovery (Recovery Flow)

**Feature Goal:** Recover users who drop out of funnel

**Event Flow:**
```
abandonment_detected (drop_step identified)
  ↓ (system sends reminder)
reminder_sent (channel, hours_since_drop)
  ↓ (user receives & opens)
reminder_opened
  ↓ (user clicks through)
reminder_cta_clicked
  ↓ (user returns to app)
resumed_at_step
  ↓ (user completes)
reconverted
```

**Key Attributes to Track:**
- `drop_step` - Where user abandoned (destination_card_clicked, application_started, document_uploaded, pay_now_clicked)
- `channel` - push, email, whatsapp
- `hours_since_drop` - Timing of nudge
- Re-link events via `user_id` and `application_id`

**Critical Metrics:**
```sql
-- Recovery rate by drop step
WITH abandonment_recovery AS (
    SELECT 
        a.drop_step,
        a.user_id,
        a.application_id,
        MAX(CASE WHEN r.event = 'reconverted' THEN 1 ELSE 0 END) as recovered
    FROM events a
    LEFT JOIN events r 
        ON a.user_id = r.user_id 
        AND a.application_id = r.application_id
        AND r.event = 'reconverted'
        AND r.timestamp > a.timestamp
    WHERE a.event = 'abandonment_detected'
    GROUP BY a.drop_step, a.user_id, a.application_id
)
SELECT 
    drop_step,
    COUNT(*) as abandonments,
    SUM(recovered) as recoveries,
    ROUND(100.0 * SUM(recovered) / COUNT(*), 2) as recovery_rate
FROM abandonment_recovery
GROUP BY drop_step
ORDER BY recovery_rate DESC;

-- Channel effectiveness
SELECT 
    channel,
    COUNT(DISTINCT CASE WHEN event = 'reminder_sent' THEN user_id END) as sent,
    COUNT(DISTINCT CASE WHEN event = 'reminder_opened' THEN user_id END) as opened,
    COUNT(DISTINCT CASE WHEN event = 'reminder_cta_clicked' THEN user_id END) as clicked,
    COUNT(DISTINCT CASE WHEN event = 'reconverted' THEN user_id END) as reconverted,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event = 'reconverted' THEN user_id END) / 
          NULLIF(COUNT(DISTINCT CASE WHEN event = 'reminder_sent' THEN user_id END), 0), 2) as recovery_rate
FROM events
GROUP BY channel
ORDER BY recovery_rate DESC;

-- Optimal timing analysis
SELECT 
    CASE 
        WHEN hours_since_drop < 2 THEN '< 2h'
        WHEN hours_since_drop < 6 THEN '2-6h'
        WHEN hours_since_drop < 24 THEN '6-24h'
        WHEN hours_since_drop < 48 THEN '24-48h'
        ELSE '48h+'
    END as timing_bucket,
    COUNT(*) as reminders_sent,
    -- Join to check recovery
    SUM(CASE WHEN recovered THEN 1 ELSE 0 END) as recoveries,
    ROUND(100.0 * SUM(CASE WHEN recovered THEN 1 ELSE 0 END) / COUNT(*), 2) as recovery_rate
FROM reminder_events
GROUP BY timing_bucket
ORDER BY MIN(hours_since_drop);
```

**Anomaly Watch:**
- Reminders sent but never opened (deliverability issues)
- Opened but not clicked (weak CTA messaging)
- Clicked but not resumed (broken deep links)
- Specific drop_steps with 0% recovery (unrecoverable or bad timing)

**Design Lessons:**
- Tag abandonment event with the specific step
- Include timing metadata (hours_since_drop) for optimization
- Track full funnel: sent → opened → clicked → resumed → converted
- Use same user_id + application_id to link abandonment to recovery

---

### Pattern 5: Instant Forex Add-on (Upsell Flow)

**Feature Goal:** Increase AOV through forex add-on

**Event Flow:**
```
forex_offer_shown (fx_rate displayed)
  ↓ (user engages)
currency_selected
  ↓ (user specifies amount)
amount_entered
  ↓ (adds to cart)
forex_added_to_cart (addon_value_inr)
  ↓ (completes purchase)
forex_purchased
```

**Key Attributes to Track:**
- `from_currency`, `to_currency` - Currency pair
- `fx_rate` - Exchange rate shown
- `amount` - Foreign currency amount
- `addon_value_inr` - Revenue impact
- `destination` - Link to visa destination

**Critical Metrics:**
```sql
-- Attach rate by destination
SELECT 
    destination,
    COUNT(DISTINCT CASE WHEN event = 'forex_offer_shown' THEN application_id END) as offers_shown,
    COUNT(DISTINCT CASE WHEN event = 'forex_purchased' THEN application_id END) as forex_purchased,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN event = 'forex_purchased' THEN application_id END) / 
          NULLIF(COUNT(DISTINCT CASE WHEN event = 'forex_offer_shown' THEN application_id END), 0), 2) as attach_rate
FROM events
GROUP BY destination
ORDER BY offers_shown DESC;

-- Drop-off analysis
WITH forex_funnel AS (
    SELECT 
        application_id,
        MAX(CASE WHEN event = 'forex_offer_shown' THEN 1 ELSE 0 END) as saw_offer,
        MAX(CASE WHEN event = 'currency_selected' THEN 1 ELSE 0 END) as engaged,
        MAX(CASE WHEN event = 'amount_entered' THEN 1 ELSE 0 END) as entered_amount,
        MAX(CASE WHEN event = 'forex_added_to_cart' THEN 1 ELSE 0 END) as added_to_cart,
        MAX(CASE WHEN event = 'forex_purchased' THEN 1 ELSE 0 END) as purchased
    FROM events
    GROUP BY application_id
)
SELECT 
    SUM(saw_offer) as step_1_offer,
    SUM(engaged) as step_2_engaged,
    SUM(entered_amount) as step_3_amount,
    SUM(added_to_cart) as step_4_cart,
    SUM(purchased) as step_5_purchased,
    ROUND(100.0 * SUM(engaged) / NULLIF(SUM(saw_offer), 0), 2) as offer_to_engage,
    ROUND(100.0 * SUM(added_to_cart) / NULLIF(SUM(entered_amount), 0), 2) as amount_to_cart,
    ROUND(100.0 * SUM(purchased) / NULLIF(SUM(added_to_cart), 0), 2) as cart_to_purchase
FROM forex_funnel;

-- AOV impact
SELECT 
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY addon_value_inr) as p25_addon_value,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY addon_value_inr) as median_addon_value,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY addon_value_inr) as p75_addon_value,
    AVG(addon_value_inr) as avg_addon_value,
    SUM(addon_value_inr) as total_addon_revenue
FROM events
WHERE event = 'forex_purchased';
```

**Anomaly Watch:**
- High engagement (currency_selected) but low amount_entered (UX friction)
- Added to cart but not purchased (payment failures or cart abandonment)
- Forex rate anomalies (very high/low rates suggesting data issues)
- addon_value_inr inconsistent with amount × fx_rate

**Design Lessons:**
- Track every micro-step in the upsell flow for granular drop-off analysis
- Include pricing context (fx_rate, amount, value) for validation
- Link to primary transaction (destination, application_id)
- Distinguish engagement (viewed offer) from intent (added to cart)

---

## Cross-Pattern Insights

### Combining Patterns

**Express Checkout + Forex:**
```sql
-- Do users who use express checkout also buy forex?
WITH user_features AS (
    SELECT 
        user_id,
        MAX(CASE WHEN event LIKE 'express_%' THEN 1 ELSE 0 END) as used_express,
        MAX(CASE WHEN event LIKE 'forex_%' THEN 1 ELSE 0 END) as used_forex
    FROM events
    GROUP BY user_id
)
SELECT 
    used_express,
    COUNT(*) as users,
    SUM(used_forex) as also_used_forex,
    ROUND(100.0 * SUM(used_forex) / COUNT(*), 2) as forex_adoption_rate
FROM user_features
GROUP BY used_express;
```

**Group Applications + Status Sharing:**
```sql
-- Do group travelers share more?
SELECT 
    CASE WHEN used_group THEN 'Group Traveller' ELSE 'Solo Traveller' END as traveller_type,
    COUNT(*) as users,
    SUM(shared_status) as sharers,
    ROUND(100.0 * SUM(shared_status) / COUNT(*), 2) as share_rate
FROM (
    SELECT 
        user_id,
        MAX(CASE WHEN event LIKE 'group_%' THEN 1 ELSE 0 END) as used_group,
        MAX(CASE WHEN event = 'share_clicked' THEN 1 ELSE 0 END) as shared_status
    FROM events
    GROUP BY user_id
) user_behavior
GROUP BY used_group;
```

---

## Event Schema Patterns Observed

### Pattern: Incremental Event Detail
- Early events (shown, clicked) have minimal data
- Later events (confirmed, purchased) have rich nested objects
- This is correct: collect more context as user progresses

### Pattern: State Flags in Events
- `otp_success` (boolean) - Outcome flag
- `docs_complete` (boolean) - Readiness flag
- `recipient_is_new_user` (boolean) - Attribution flag

### Pattern: Linking Identifiers
- `user_id` - Links events for same user
- `application_id` - Links events for same transaction
- `group_id` - Links events for same group
- `share_id` - Links sharer and recipient events

### Pattern: Nested Context Objects
```json
"payment": {
  "amount": 5596.0,
  "currency": "INR",
  "latency_ms": 3879
}
```
Groups related attributes that always travel together

---

## Common Data Issues Found

1. **Null OS values** - Some Android events have `os: null`
   - Impact: Breaks OS-based segmentation
   - Fix: Device type fallback, SDK validation

2. **Device type inconsistency** - "Desktop", "web-user-b2c", "ios", "android"
   - Impact: Segmentation requires normalization
   - Fix: Mapping table or normalized dimension

3. **Currency ambiguity** - Field named `addon_value_inr` but users in multiple currencies
   - Impact: Suggests INR-centric design
   - Fix: Separate amount and display_currency fields

4. **Timestamp precision** - Events rounded to minute boundaries
   - Impact: Difficult to sequence events within same minute
   - Fix: Add millisecond precision or sequence numbers

---

## Quick Decision Tree: Which Pattern Am I Looking At?

```
Is the feature part of the core conversion funnel?
├─ YES → Linear Funnel Pattern
│         (Express Checkout, Standard Checkout)
│
└─ NO → Is it a repeatable action within a session?
    ├─ YES → Iterative Loop Pattern
    │         (Group Applications, Multi-item Cart)
    │
    └─ NO → Does it involve multiple users?
        ├─ YES → Viral Loop Pattern
        │         (Status Sharing, Referrals)
        │
        └─ NO → Is it triggered by user inactivity?
            ├─ YES → Recovery Flow Pattern
            │         (Abandoned Checkout, Re-engagement)
            │
            └─ NO → Is it an optional add-on?
                ├─ YES → Upsell Flow Pattern
                │         (Forex, Insurance, Expedited Processing)
                │
                └─ Other pattern (document it!)
```

---

## Reusable SQL Snippets

### Snippet 1: Basic Funnel
```sql
WITH funnel AS (
    SELECT 
        user_id,
        MAX(CASE WHEN event = 'step_1' THEN 1 ELSE 0 END) as s1,
        MAX(CASE WHEN event = 'step_2' THEN 1 ELSE 0 END) as s2,
        MAX(CASE WHEN event = 'step_3' THEN 1 ELSE 0 END) as s3
    FROM events
    GROUP BY user_id
)
SELECT 
    SUM(s1) as step_1_users,
    SUM(s2) as step_2_users,
    SUM(s3) as step_3_users,
    ROUND(100.0 * SUM(s3) / NULLIF(SUM(s1), 0), 2) as conversion_rate
FROM funnel;
```

### Snippet 2: Segment Comparison
```sql
SELECT 
    segment,
    COUNT(DISTINCT user_id) as users,
    COUNT(DISTINCT CASE WHEN converted THEN user_id END) as converters,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN converted THEN user_id END) / 
          COUNT(DISTINCT user_id), 2) as conversion_rate
FROM user_segments
GROUP BY segment
ORDER BY conversion_rate DESC;
```

### Snippet 3: Time-Series Trend
```sql
SELECT 
    DATE(timestamp) as date,
    COUNT(*) as events,
    COUNT(DISTINCT user_id) as unique_users,
    AVG(COUNT(*)) OVER (
        ORDER BY DATE(timestamp) 
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) as moving_avg_7d
FROM events
GROUP BY DATE(timestamp)
ORDER BY date;
```

---

**End of Pattern Reference**

Use this alongside ANALYTICS_KNOWLEDGE_BASE.md for comprehensive analysis guidance.
