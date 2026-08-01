import hashlib
import json
import re
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts.models import AnalyticsContract
from app.contracts.prompts import (
    PROMPT_VERSION,
    build_generation_request,
    build_repair_request,
)
from app.contracts.validator import contract_warnings, validate_contract_grounding
from app.core.logging import get_logger
from app.core.tracing import InstrumentationTracer, NullInstrumentationTracer
from app.llm.provider import StructuredGenerationProvider
from app.profiling.models import SourceProfile

logger = get_logger(__name__)
MAX_REPAIR_ATTEMPTS = 2


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractValidationIssue(AgentModel):
    code: str
    path: str
    message: str


class ContractGenerationResult(AgentModel):
    run_id: str
    trace_id: str
    feature_slug: str
    validation_status: Literal["valid", "blocked"]
    analytics_contract: AnalyticsContract | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[ContractValidationIssue] = Field(default_factory=list)
    attempts: int = Field(ge=1, le=MAX_REPAIR_ATTEMPTS + 1)


class InstrumentationAgent:
    def __init__(
        self,
        provider: StructuredGenerationProvider,
        *,
        context_max_chars: int = 8_000,
    ) -> None:
        self._provider = provider
        self._context_max_chars = context_max_chars

    async def generate_contract(
        self,
        feature_spec: str,
        source_profile: SourceProfile,
        *,
        context_summary: str | None = None,
        tracer: InstrumentationTracer | None = None,
        run_id: uuid.UUID | None = None,
    ) -> ContractGenerationResult:
        active_run_id = run_id or uuid.uuid4()
        trace_id = active_run_id.hex
        feature_slug = feature_slug_from_spec(feature_spec)
        spec_sha256 = hashlib.sha256(feature_spec.encode("utf-8")).hexdigest()
        bounded_context = (
            context_summary[: self._context_max_chars] if context_summary is not None else None
        )
        active_tracer = tracer or NullInstrumentationTracer()
        base_metadata = {
            "run_id": str(active_run_id),
            "feature_slug": feature_slug,
            "source_checksum": source_profile.file.sha256,
            "model": self._provider.model_name,
            "prompt_version": PROMPT_VERSION,
        }
        initial_request = build_generation_request(
            feature_spec,
            source_profile,
            spec_sha256=spec_sha256,
            expected_feature_slug=feature_slug,
            context_summary=bounded_context,
        )
        request = initial_request
        invalid_candidate = ""
        validation_errors: list[ContractValidationIssue] = []
        attempt_count = 0

        with active_tracer.observe(
            "instrumentation_agent",
            as_type="span",
            metadata={
                **base_metadata,
                "attempt_number": 1,
                "validation_status": "pending",
                "latency_ms": 0,
            },
        ) as agent_observation:
            agent_started = time.perf_counter()
            for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
                attempt_count = attempt + 1
                if attempt:
                    request = build_repair_request(
                        initial_request,
                        invalid_candidate=invalid_candidate,
                        validation_errors=[error.model_dump() for error in validation_errors],
                    )

                generation_name = "contract_generation" if attempt == 0 else "contract_repair"
                generation_metadata = {
                    **base_metadata,
                    "attempt_number": attempt + 1,
                    "validation_status": "pending",
                    "latency_ms": 0,
                }
                try:
                    with active_tracer.observe(
                        generation_name,
                        as_type="generation",
                        metadata=generation_metadata,
                        model=self._provider.model_name,
                    ) as generation_observation:
                        response = await self._provider.generate(request)
                        invalid_candidate = response.content
                        generation_observation.update(
                            metadata={
                                **generation_metadata,
                                "model": response.model,
                                "latency_ms": response.latency_ms,
                            },
                            model=response.model,
                            usage_details=(
                                response.usage.as_langfuse() if response.usage is not None else None
                            ),
                        )
                except Exception as exc:
                    logger.warning(
                        "contract_provider_failed",
                        extra={"run_id": str(active_run_id), "error_type": type(exc).__name__},
                    )
                    validation_errors = [
                        ContractValidationIssue(
                            code="provider_error",
                            path="provider",
                            message="structured generation provider failed",
                        )
                    ]
                    break

                validation_started = time.perf_counter()
                with active_tracer.observe(
                    "contract_validation",
                    as_type="span",
                    metadata={
                        **base_metadata,
                        "attempt_number": attempt + 1,
                        "validation_status": "pending",
                        "latency_ms": 0,
                    },
                ) as validation_observation:
                    contract, validation_errors = _validate_candidate(
                        invalid_candidate,
                        source_profile,
                        feature_spec,
                        spec_sha256=spec_sha256,
                        expected_feature_slug=feature_slug,
                    )
                    validation_status = "valid" if contract is not None else "invalid"
                    validation_observation.update(
                        metadata={
                            **base_metadata,
                            "attempt_number": attempt + 1,
                            "validation_status": validation_status,
                            "latency_ms": round((time.perf_counter() - validation_started) * 1000),
                        }
                    )

                if contract is not None:
                    result = ContractGenerationResult(
                        run_id=str(active_run_id),
                        trace_id=trace_id,
                        feature_slug=feature_slug,
                        validation_status="valid",
                        analytics_contract=contract,
                        warnings=contract_warnings(contract),
                        attempts=attempt + 1,
                    )
                    agent_observation.update(
                        metadata={
                            **base_metadata,
                            "attempt_number": attempt + 1,
                            "validation_status": "valid",
                            "latency_ms": round((time.perf_counter() - agent_started) * 1000),
                        }
                    )
                    return result

            agent_observation.update(
                metadata={
                    **base_metadata,
                    "attempt_number": attempt_count,
                    "validation_status": "blocked",
                    "latency_ms": round((time.perf_counter() - agent_started) * 1000),
                }
            )
            return ContractGenerationResult(
                run_id=str(active_run_id),
                trace_id=trace_id,
                feature_slug=feature_slug,
                validation_status="blocked",
                warnings=["contract generation blocked after validation/repair failure"],
                errors=validation_errors,
                attempts=attempt_count,
            )


def feature_slug_from_spec(feature_spec: str) -> str:
    title = next(
        (
            match.group(1).strip()
            for line in feature_spec.splitlines()
            if (match := re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line))
        ),
        "feature",
    )
    slug = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")
    if any(marker in slug for marker in ("api_key", "credential", "password", "secret", "token")):
        return "feature"
    return slug or "feature"


def _validate_candidate(
    candidate: str,
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    spec_sha256: str,
    expected_feature_slug: str,
) -> tuple[AnalyticsContract | None, list[ContractValidationIssue]]:
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, [
            ContractValidationIssue(
                code="invalid_json",
                path="$",
                message=f"candidate is not valid JSON at line {exc.lineno} column {exc.colno}",
            )
        ]
    if not isinstance(value, dict):
        return None, [
            ContractValidationIssue(
                code="invalid_json_type",
                path="$",
                message="candidate JSON must be an object",
            )
        ]

    try:
        contract = AnalyticsContract.model_validate_with_profile(value, source_profile)
    except ValidationError as exc:
        return None, [
            ContractValidationIssue(
                code=error["type"],
                path=".".join(str(part) for part in error["loc"]) or "$",
                message=error["msg"],
            )
            for error in exc.errors(include_input=False, include_url=False)
        ]

    grounding_errors = validate_contract_grounding(
        contract,
        source_profile,
        feature_spec,
        spec_sha256=spec_sha256,
        expected_feature_slug=expected_feature_slug,
    )
    if grounding_errors:
        return None, [ContractValidationIssue(**error.as_dict()) for error in grounding_errors]
    return contract, []
