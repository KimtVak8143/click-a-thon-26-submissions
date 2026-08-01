# Recipient Status Sharing

The ordered share workflow is `status_shared` -> `status_share_opened`, linked by `share_id`.
Recipient opens intentionally do not emit `user_id`; `share_id` is the workflow key.

## Questions the PM will ask

- What share reaches status_share_opened from status_shared?
- How does opening vary by recipient_type and channel?
