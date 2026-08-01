# Checkout Widget

Ordered user actions: `checkout_widget_shown` -> `checkout_widget_selected` ->
`stored_method_applied` -> `verification_submitted` -> `checkout_widget_confirmed`.
The workflow key is `application_id`.

## Questions the PM will ask

- What share reaches checkout_widget_confirmed from checkout_widget_shown?
- Where does verification_ok fail by device_type and os?
- How long does processing_ms take between the first and last event?
