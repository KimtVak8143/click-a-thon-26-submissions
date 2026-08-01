# Document Verification

Ordered user actions: `doc_scan_started` -> `doc_scan_completed` -> `doc_review_passed`, linked
by `verification_id`. This feature has no purchase or session events.

## Questions the PM will ask

- What share reaches doc_review_passed from doc_scan_started?
- Where does verification_passed fail by device_type and os?
- How long does scan_processing_ms take from doc_scan_started to doc_scan_completed?
