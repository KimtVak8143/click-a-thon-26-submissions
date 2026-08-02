# Feature Pattern Reference Guide

**Version:** 1.0  
**Based on:** Atlys product feature examples  
**Purpose:** Pattern matching for new product instrumentation and analysis.

## Linear funnel: Express Checkout

Representative flow: `express_checkout_shown` → `express_checkout_selected` →
`saved_method_used` → `otp_entered` → `express_payment_confirmed`.

Track price/currency, saved method, OTP attempts and success, and payment latency. Analyze overall
and incremental conversion, OTP success by platform, and latency. Treat OTP success below 90%,
payment latency above 10 seconds, or platform gaps above 15% as investigation heuristics—not
universal targets or conclusions.

Evidence: `feature_patterns:v1:linear_funnel`.

## Iterative loop: Group and Family Applications

Representative flow: `group_started` → (`traveller_added` / `traveller_removed`)* →
`group_submitted`.

Link with `group_id`; preserve declared `group_size`, `traveller_index`, relationship,
`docs_complete`, and submitted count. Analyze completion by group size, add/remove churn, document
readiness blockers, and mismatch between declared and submitted sizes.

Evidence: `feature_patterns:v1:iterative_loop`.

## Viral loop: Visa Status Sharing

Representative sharer flow: `share_clicked` → `channel_selected` → `link_generated`.
Representative recipient flow: `link_opened` → `recipient_cta_clicked`.

Use `share_id` to connect sides before a recipient identity exists. Track shared status, channel,
new-user attribution, CTA, open rate, recipient conversion, and K-factor. Investigate generated
links with no opens, extreme repeated opens, channel attribution gaps, and broken recipient paths.

Evidence: `feature_patterns:v1:viral_loop`.

## Recovery flow: Abandoned Checkout

Representative flow: `abandonment_detected` → `reminder_sent` → `reminder_opened` →
`reminder_cta_clicked` → `resumed_at_step` → `reconverted`.

Link by `user_id` and `application_id`; track `drop_step`, channel, and hours since abandonment.
Analyze recovery by drop step, channel, and timing. Separate delivery, message, deep-link, resume,
and conversion failures.

Evidence: `feature_patterns:v1:recovery_flow`.

## Upsell flow: Instant Forex Add-on

Representative flow: `forex_offer_shown` → `currency_selected` → `amount_entered` →
`forex_added_to_cart` → `forex_purchased`.

Track source/target currency, displayed FX rate, amount, add-on value, destination, and the primary
application. Analyze attach rate, step drop-off, add-on value distribution, and AOV impact. Validate
that currency, amount, rate, and converted value are internally consistent.

Evidence: `feature_patterns:v1:upsell_flow`.

## Multi-path and cross-feature analysis

When several routes reach one goal, compare eligible populations and conversion by path instead of
forcing a single sequence. Cross-feature interaction analysis may compare co-adoption and sequence,
but it must use a stable user bridge and compatible observation windows.

Evidence: `feature_patterns:v1:multi_path`, `feature_patterns:v1:cross_feature`.

## Classification rules

- Sequential steps toward a goal: `linear_funnel`.
- Repeated add/edit/remove actions before submission: `iterative_loop`.
- Sharer and recipient sides linked across identities: `viral_loop`.
- Inactivity or failure followed by a nudge and return: `recovery_flow`.
- Optional offer attached to a primary purchase: `upsell_flow`.
- Multiple routes to the same goal: `multi_path`.

The classification guides metrics and questions. Observed events and the feature specification
remain authoritative; never fabricate a missing event to make a feature fit a known pattern.
