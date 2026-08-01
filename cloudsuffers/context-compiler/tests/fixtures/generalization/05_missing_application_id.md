# Session Recovery

No application identifier is emitted. The ordered workflow is `checkout_abandoned` ->
`checkout_recovered`, and `recovery_session_id` is the stable key shared by both events.

## Questions the PM will ask

- What share of recovery sessions reach checkout_recovered?
- How does recovery vary by channel?
