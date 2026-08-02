# 5. Standard probe set

The following prompts are the standard probe set the judges will use to exercise the Analytics Agent against the existing tables. The outputs should be captured and linked into the submission bundle.

## Probe 1

Prompt:

> Analyze the existing funnel and surface the most important issues, with the why.

Expected output:

- a funnel-level summary of the main conversion losses;
- an explanation of why each issue likely matters;
- references to the underlying ClickHouse evidence.

## Probe 2

Prompt:

> Where are we losing conversions, and for which segments (device / geo / destination)?

Expected output:

- a segmentation analysis across device, geography, and destination;
- a short list of the biggest drop-off segments;
- evidence-backed interpretations.

## Probe 3

Prompt:

> Are there any regressions or trends over the last quarter?

Expected output:

- trend or regression analysis across recent time windows;
- identified changes in conversion or engagement rate;
- reasons the trend may be occurring.

## Probe 4

Prompt:

> Is anything in the base context wrong, stale, or self-contradictory?

Expected output:

- a self-audit of the approved context layer;
- a set of issues, contradictions, or stale claims;
- a short recommendation for how to update the context.

## Output storage

Suggested storage location:

- [probe_outputs/probe_01.md](probe_outputs/probe_01.md)
- [probe_outputs/probe_02.md](probe_outputs/probe_02.md)
- [probe_outputs/probe_03.md](probe_outputs/probe_03.md)
- [probe_outputs/probe_04.md](probe_outputs/probe_04.md)
