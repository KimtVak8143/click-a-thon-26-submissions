from dataclasses import dataclass
from typing import Any

EXPECTED_CONTRACT_INTENT_ENVELOPE = "ContractIntent"
_CONTRACT_INTENT_ENVELOPE_NAMES = frozenset({"ContractIntent", "contract_intent", "contractintent"})


@dataclass(frozen=True)
class EnvelopeDecodeResult:
    value: dict[str, Any]
    provider_envelope_unwrapped: bool = False
    provider_envelope_name: str | None = None


def decode_contract_intent_envelope(value: dict[str, Any]) -> EnvelopeDecodeResult:
    """Unwrap only the recognized, single-key ContractIntent transport envelope."""

    if len(value) != 1:
        return EnvelopeDecodeResult(value=value)
    name, inner = next(iter(value.items()))
    if name not in _CONTRACT_INTENT_ENVELOPE_NAMES or not isinstance(inner, dict):
        return EnvelopeDecodeResult(value=value)
    return EnvelopeDecodeResult(
        value=inner,
        provider_envelope_unwrapped=True,
        provider_envelope_name=EXPECTED_CONTRACT_INTENT_ENVELOPE,
    )
