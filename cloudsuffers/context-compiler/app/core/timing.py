import logging
import math
from typing import Any

_SAFE_TIMING_FIELDS = {
    "stage",
    "elapsed_ms",
    "prompt_bytes",
    "estimated_prompt_tokens",
    "json_schema_bytes",
    "provider_schema_bytes",
    "schema_references_before",
    "schema_references_after",
    "nested_required_array_count",
    "profile_summary_bytes",
    "response_bytes",
    "intent_bytes",
    "event_count",
    "field_count",
    "entity_count",
    "funnel_count",
    "metric_count",
    "dimension_count",
    "attempt_number",
    "model",
    "provider_status",
    "error_type",
    "status_code",
    "provider_envelope_unwrapped",
    "provider_envelope_name",
    "candidate_hash",
}


def estimate_prompt_tokens(prompt_bytes: int) -> int:
    """Tokenizer-independent estimate used only for safe observability."""
    return math.ceil(prompt_bytes / 4)


def timing_metadata(**values: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if key in _SAFE_TIMING_FIELDS and value is not None
    }


def log_timing(logger: logging.Logger, event: str, **values: Any) -> None:
    logger.info(event, extra=timing_metadata(**values))
