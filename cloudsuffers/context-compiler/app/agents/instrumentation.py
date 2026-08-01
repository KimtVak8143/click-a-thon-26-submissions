import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts.compiler import compile_contract_payload
from app.contracts.intent import (
    ContractIntent,
    semantic_contract_requirements,
    validate_intent_grounding,
)
from app.contracts.models import AnalyticsContract
from app.contracts.prompts import (
    PROMPT_VERSION,
    build_generation_request,
    build_repair_request,
)
from app.contracts.validator import contract_warnings, validate_contract_grounding
from app.core.logging import get_logger
from app.core.timing import log_timing, timing_metadata
from app.core.tracing import InstrumentationTracer, NullInstrumentationTracer
from app.llm.envelope import decode_contract_intent_envelope
from app.llm.provider import (
    ProviderError,
    ProviderFailureCategory,
    StructuredGenerationProvider,
    StructuredGenerationRequest,
)
from app.profiling.models import SourceProfile

logger = get_logger(__name__)
MAX_REPAIR_ATTEMPTS = 3

_REPAIR_ARRAY_FIELDS = (
    "entities",
    "funnels",
    "metrics",
    "dimensions",
    "relationships",
    "observations",
    "assumptions",
    "open_questions",
)
_REPAIR_SCALAR_FIELDS = ("feature", "primary_entity_id", "grain")


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractValidationIssue(AgentModel):
    code: str
    path: str
    message: str
    status_code: int | None = None
    provider_error_code: str | None = None


class ContractGenerationResult(AgentModel):
    run_id: str
    trace_id: str
    feature_slug: str
    validation_status: Literal["valid", "blocked"]
    analytics_contract: AnalyticsContract | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[ContractValidationIssue] = Field(default_factory=list)
    attempts: int = Field(ge=0, le=MAX_REPAIR_ATTEMPTS + 1)
    context_version_id: UUID | None = None
    context_content_sha256: str | None = None


class InstrumentationAgent:
    def __init__(
        self,
        provider: StructuredGenerationProvider,
        *,
        context_max_chars: int = 8_000,
        total_timeout_seconds: float = 600,
    ) -> None:
        self._provider = provider
        self._context_max_chars = context_max_chars
        self._total_timeout_seconds = total_timeout_seconds

    async def generate_contract(
        self,
        feature_spec: str,
        source_profile: SourceProfile,
        *,
        context_summary: str | None = None,
        context_version_id: UUID | None = None,
        context_content_sha256: str | None = None,
        context_evidence_ids: list[str] | None = None,
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
            "context_version_id": str(context_version_id) if context_version_id else None,
            "context_content_sha256": context_content_sha256,
        }
        attempt_state = {"count": 0}
        started = time.perf_counter()
        initial_timing = {
            "stage": "total_generation",
            "attempt_number": 1,
            "model": self._provider.model_name,
            "provider_status": "started",
        }
        log_timing(logger, "generation_started", **initial_timing)

        with active_tracer.observe(
            "total_generation",
            as_type="span",
            input={"feature_slug": feature_slug, "run_id": str(active_run_id)},
            metadata={**base_metadata, **timing_metadata(**initial_timing)},
            tags=["contract-generation"],
        ) as total_observation:
            try:
                async with asyncio.timeout(self._total_timeout_seconds):
                    result = await self._generate_within_budget(
                        feature_spec,
                        source_profile,
                        bounded_context=bounded_context,
                        context_version_id=context_version_id,
                        context_content_sha256=context_content_sha256,
                        context_evidence_ids=context_evidence_ids or [],
                        spec_sha256=spec_sha256,
                        feature_slug=feature_slug,
                        run_id=active_run_id,
                        trace_id=trace_id,
                        tracer=active_tracer,
                        base_metadata=base_metadata,
                        attempt_state=attempt_state,
                    )
            except TimeoutError:
                elapsed_ms = _elapsed_ms(started)
                result = self._blocked_result(
                    active_run_id,
                    trace_id,
                    feature_slug,
                    attempts=attempt_state["count"],
                    issue=ContractValidationIssue(
                        code=ProviderFailureCategory.PROVIDER_TIMEOUT.value,
                        path="pipeline",
                        message="total contract generation time budget exceeded",
                    ),
                    warning="contract generation stopped after reaching the total time budget",
                )
                failed = {
                    "stage": "total_generation",
                    "elapsed_ms": elapsed_ms,
                    "attempt_number": max(attempt_state["count"], 1),
                    "model": self._provider.model_name,
                    "provider_status": ProviderFailureCategory.PROVIDER_TIMEOUT.value,
                    "error_type": ProviderFailureCategory.PROVIDER_TIMEOUT.value,
                }
                log_timing(logger, "generation_failed", **failed)
                total_observation.update(metadata={**base_metadata, **timing_metadata(**failed)})
                return result
            except asyncio.CancelledError:
                failed = {
                    "stage": "total_generation",
                    "elapsed_ms": _elapsed_ms(started),
                    "attempt_number": max(attempt_state["count"], 1),
                    "model": self._provider.model_name,
                    "provider_status": ProviderFailureCategory.CANCELLED.value,
                    "error_type": ProviderFailureCategory.CANCELLED.value,
                }
                log_timing(logger, "generation_failed", **failed)
                total_observation.update(metadata={**base_metadata, **timing_metadata(**failed)})
                raise

            completed = {
                "stage": "total_generation",
                "elapsed_ms": _elapsed_ms(started),
                "attempt_number": max(attempt_state["count"], 1),
                "model": self._provider.model_name,
                "provider_status": result.validation_status,
            }
            log_timing(
                logger,
                "generation_completed"
                if result.validation_status == "valid"
                else "generation_failed",
                **completed,
            )
            total_observation.update(metadata={**base_metadata, **timing_metadata(**completed)})
            return result

    async def _generate_within_budget(
        self,
        feature_spec: str,
        source_profile: SourceProfile,
        *,
        bounded_context: str | None,
        context_version_id: UUID | None,
        context_content_sha256: str | None,
        context_evidence_ids: list[str],
        spec_sha256: str,
        feature_slug: str,
        run_id: uuid.UUID,
        trace_id: str,
        tracer: InstrumentationTracer,
        base_metadata: dict[str, Any],
        attempt_state: dict[str, int],
    ) -> ContractGenerationResult:
        prompt_started = time.perf_counter()
        with tracer.observe(
            "prompt_construction",
            as_type="span",
            input={"feature_slug": feature_slug, "context_available": bounded_context is not None},
            metadata={
                **base_metadata,
                **timing_metadata(
                    stage="prompt_construction",
                    attempt_number=1,
                    model=self._provider.model_name,
                    provider_status="started",
                ),
            },
        ) as prompt_observation:
            initial_request = build_generation_request(
                feature_spec,
                source_profile,
                expected_feature_slug=feature_slug,
                context_summary=bounded_context,
                context_evidence_ids=context_evidence_ids,
            )
            prompt_timing = _request_timing(
                initial_request,
                stage="prompt_construction",
                elapsed_ms=_elapsed_ms(prompt_started),
                provider_status="completed",
                model=self._provider.model_name,
            )
            log_timing(logger, "stage_completed", **prompt_timing)
            prompt_observation.update(
                metadata={**base_metadata, **timing_metadata(**prompt_timing)}
            )

        invalid_candidate = ""
        candidate_received = False
        parsed_candidate: dict[str, Any] | None = None
        validation_errors: list[ContractValidationIssue] = []
        transport_warnings: list[str] = []
        seen_candidate_hashes: set[str] = set()
        seen_error_signatures: set[str] = set()
        agent_started = time.perf_counter()
        with tracer.observe(
            "instrumentation_agent",
            as_type="agent",
            input={
                "feature_slug": feature_slug,
                "event_count": source_profile.file.valid_row_count,
            },
            metadata={
                **base_metadata,
                **timing_metadata(
                    stage="instrumentation_agent",
                    attempt_number=1,
                    model=self._provider.model_name,
                    provider_status="started",
                ),
            },
            tags=["instrumentation"],
        ) as agent_observation:
            for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
                attempt_number = attempt + 1
                attempt_state["count"] = attempt_number
                previous_validation_errors = list(validation_errors)
                request = initial_request
                generation_name = "intent_generation"
                envelope_metadata: dict[str, Any] = {}
                repeated_error_signature = False
                repair_scope: dict[str, Any] | None = None
                repair_source_candidate: dict[str, Any] | None = None
                if attempt:
                    if not candidate_received:
                        break
                    generation_name = "intent_repair"
                    repair_source_candidate = parsed_candidate
                    repair_scope = _build_repair_scope(parsed_candidate, validation_errors)
                    request = build_repair_request(
                        initial_request,
                        invalid_candidate=invalid_candidate,
                        validation_errors=[item.model_dump() for item in validation_errors],
                        allowed_observed_events=sorted(source_profile.event_names),
                        allowed_field_paths=sorted(source_profile.field_paths),
                        allowed_declared_entity_ids=_declared_entity_ids(parsed_candidate),
                        repair_scope=repair_scope,
                        deterministic_repair_hints=_build_deterministic_repair_hints(
                            validation_errors,
                            source_profile,
                            feature_spec,
                        ),
                        attempt_number=attempt_number,
                    )

                with tracer.observe(
                    generation_name,
                    as_type="generation",
                    input={"attempt": attempt_number, "schema_name": request.schema_name},
                    metadata={
                        **base_metadata,
                        **timing_metadata(
                            **_request_timing(
                                request,
                                stage=generation_name,
                                elapsed_ms=0,
                                provider_status="started",
                                model=self._provider.model_name,
                            )
                        ),
                    },
                    model=self._provider.model_name,
                    tags=[generation_name, f"attempt-{attempt_number}"],
                ) as generation_observation:
                    try:
                        provider_result = await self._request_provider(request)
                    except asyncio.CancelledError:
                        log_timing(
                            logger,
                            "stage_failed",
                            **_request_timing(
                                request,
                                stage=generation_name,
                                elapsed_ms=0,
                                provider_status=ProviderFailureCategory.CANCELLED.value,
                                error_type=ProviderFailureCategory.CANCELLED.value,
                                model=self._provider.model_name,
                            ),
                        )
                        raise
                    if isinstance(provider_result, ContractValidationIssue):
                        generation_observation.update(
                            output={"validation_status": "provider_error"},
                            metadata={
                                **base_metadata,
                                **timing_metadata(
                                    **_request_timing(
                                        request,
                                        stage=generation_name,
                                        elapsed_ms=0,
                                        provider_status=provider_result.code,
                                        error_type=provider_result.code,
                                        status_code=provider_result.status_code,
                                        model=self._provider.model_name,
                                    )
                                ),
                            },
                            level="ERROR",
                            status_message=provider_result.code,
                        )
                        log_timing(
                            logger,
                            "stage_failed",
                            **_request_timing(
                                request,
                                stage=generation_name,
                                elapsed_ms=0,
                                provider_status=provider_result.code,
                                error_type=provider_result.code,
                                status_code=provider_result.status_code,
                                model=self._provider.model_name,
                            ),
                        )
                        result = self._blocked_result(
                            run_id,
                            trace_id,
                            feature_slug,
                            attempts=attempt_number,
                            issue=provider_result,
                            warning="contract generation stopped after a provider failure",
                        )
                        result.warnings.extend(transport_warnings)
                        _finish_agent_observation(
                            agent_observation,
                            base_metadata,
                            agent_started,
                            attempt_number,
                            result.validation_status,
                        )
                        return result

                    response = provider_result
                    candidate_received = True
                    invalid_candidate = response.content
                    parsed_candidate, validation_errors = _parse_candidate_with_trace(
                        response.content,
                        response.model,
                        attempt_number,
                        tracer,
                        base_metadata,
                    )
                    intent = None
                    if parsed_candidate is not None:
                        decoded = decode_contract_intent_envelope(parsed_candidate)
                        parsed_candidate = decoded.value
                        if decoded.provider_envelope_unwrapped:
                            envelope_metadata = {
                                "provider_envelope_unwrapped": True,
                                "provider_envelope_name": decoded.provider_envelope_name,
                            }
                            warning = (
                                "provider structured-output envelope ContractIntent was unwrapped"
                            )
                            if warning not in transport_warnings:
                                transport_warnings.append(warning)
                            logger.warning(
                                "provider_envelope_unwrapped",
                                extra=timing_metadata(
                                    stage="provider_transport_normalization",
                                    attempt_number=attempt_number,
                                    model=response.model,
                                    **envelope_metadata,
                                ),
                            )
                        candidate_hash = _normalized_candidate_hash(parsed_candidate)
                        envelope_metadata["candidate_hash"] = candidate_hash
                        if attempt and candidate_hash in seen_candidate_hashes:
                            duplicate_timing = {
                                **_request_timing(
                                    request,
                                    stage=generation_name,
                                    elapsed_ms=response.latency_ms,
                                    response_bytes=len(response.content.encode("utf-8")),
                                    provider_status="non_progressing_repair",
                                    error_type="non_progressing_repair",
                                    model=response.model,
                                ),
                                **envelope_metadata,
                            }
                            generation_observation.update(
                                output={
                                    "response_bytes": len(response.content.encode("utf-8")),
                                    "validation_status": "non_progressing_repair",
                                },
                                metadata={
                                    **base_metadata,
                                    **timing_metadata(**duplicate_timing),
                                },
                                model=response.model,
                                usage_details=(
                                    response.usage.as_langfuse()
                                    if response.usage is not None
                                    else None
                                ),
                            )
                            log_timing(logger, "stage_failed", **duplicate_timing)
                            result = ContractGenerationResult(
                                run_id=str(run_id),
                                trace_id=trace_id,
                                feature_slug=feature_slug,
                                validation_status="blocked",
                                warnings=[
                                    "contract generation stopped because repair made no progress",
                                    *transport_warnings,
                                ],
                                errors=[
                                    ContractValidationIssue(
                                        code="non_progressing_repair",
                                        path="$",
                                        message=(
                                            "repair returned a normalized candidate identical to "
                                            "an earlier attempt"
                                        ),
                                    ),
                                    *previous_validation_errors,
                                ],
                                attempts=attempt_number,
                            )
                            _finish_agent_observation(
                                agent_observation,
                                base_metadata,
                                agent_started,
                                attempt_number,
                                result.validation_status,
                            )
                            return result
                        seen_candidate_hashes.add(candidate_hash)
                        intent, validation_errors = _validate_intent_with_trace(
                            parsed_candidate,
                            source_profile,
                            feature_spec,
                            expected_feature_slug=feature_slug,
                            context_summary=bounded_context,
                            context_evidence_ids=set(context_evidence_ids),
                            response_bytes=len(response.content.encode("utf-8")),
                            model=response.model,
                            attempt_number=attempt_number,
                            tracer=tracer,
                            base_metadata=base_metadata,
                            envelope_metadata=envelope_metadata,
                        )
                        if attempt and repair_scope is not None:
                            preservation_errors = _repair_preservation_errors(
                                repair_source_candidate, parsed_candidate, repair_scope
                            )
                            if preservation_errors:
                                intent = None
                                validation_errors.extend(preservation_errors)
                        if intent is None and validation_errors:
                            error_signature = _normalized_error_signature(validation_errors)
                            envelope_metadata["semantic_error_signature"] = error_signature
                            repeated_error_signature = (
                                attempt > 0 and error_signature in seen_error_signatures
                            )
                            seen_error_signatures.add(error_signature)

                    generation_status = "valid" if intent is not None else "invalid"
                    generation_timing = {
                        **_request_timing(
                            request,
                            stage=generation_name,
                            elapsed_ms=response.latency_ms,
                            response_bytes=len(response.content.encode("utf-8")),
                            provider_status=generation_status,
                            model=response.model,
                        ),
                        **envelope_metadata,
                    }
                    generation_observation.update(
                        output={
                            "response_bytes": len(response.content.encode("utf-8")),
                            "validation_status": generation_status,
                        },
                        metadata={
                            **base_metadata,
                            **timing_metadata(**generation_timing),
                        },
                        model=response.model,
                        usage_details=(
                            response.usage.as_langfuse() if response.usage is not None else None
                        ),
                    )
                    log_timing(
                        logger,
                        "stage_completed",
                        **generation_timing,
                    )

                if repeated_error_signature:
                    result = ContractGenerationResult(
                        run_id=str(run_id),
                        trace_id=trace_id,
                        feature_slug=feature_slug,
                        validation_status="blocked",
                        warnings=[
                            "contract generation stopped because repair repeated the same "
                            "semantic errors",
                            *transport_warnings,
                        ],
                        errors=[
                            ContractValidationIssue(
                                code="non_progressing_repair",
                                path="$",
                                message="repair repeated the normalized semantic-error signature",
                            ),
                            *validation_errors,
                        ],
                        attempts=attempt_number,
                        context_version_id=context_version_id,
                        context_content_sha256=context_content_sha256,
                    )
                    _finish_agent_observation(
                        agent_observation,
                        base_metadata,
                        agent_started,
                        attempt_number,
                        result.validation_status,
                    )
                    return result

                if intent is None:
                    continue

                payload, compilation_error = _compile_with_trace(
                    intent,
                    source_profile,
                    spec_sha256=spec_sha256,
                    context_version_id=context_version_id,
                    context_content_sha256=context_content_sha256,
                    context_evidence_ids=context_evidence_ids,
                    model=response.model,
                    attempt_number=attempt_number,
                    tracer=tracer,
                    base_metadata=base_metadata,
                )
                if compilation_error is not None:
                    result = self._blocked_result(
                        run_id,
                        trace_id,
                        feature_slug,
                        attempts=attempt_number,
                        issue=compilation_error,
                        warning="validated intent could not be compiled",
                    )
                    result.warnings.extend(transport_warnings)
                    _finish_agent_observation(
                        agent_observation,
                        base_metadata,
                        agent_started,
                        attempt_number,
                        result.validation_status,
                    )
                    return result

                contract, final_errors = _validate_final_contract_with_trace(
                    payload,
                    source_profile,
                    feature_spec,
                    spec_sha256=spec_sha256,
                    expected_feature_slug=feature_slug,
                    model=response.model,
                    attempt_number=attempt_number,
                    tracer=tracer,
                    base_metadata=base_metadata,
                )
                if contract is None:
                    result = ContractGenerationResult(
                        run_id=str(run_id),
                        trace_id=trace_id,
                        feature_slug=feature_slug,
                        validation_status="blocked",
                        warnings=[
                            "compiled contract failed final AnalyticsContract validation",
                            *transport_warnings,
                        ],
                        errors=final_errors,
                        attempts=attempt_number,
                    )
                    _finish_agent_observation(
                        agent_observation,
                        base_metadata,
                        agent_started,
                        attempt_number,
                        result.validation_status,
                    )
                    return result

                result = ContractGenerationResult(
                    run_id=str(run_id),
                    trace_id=trace_id,
                    feature_slug=feature_slug,
                    validation_status="valid",
                    analytics_contract=contract,
                    warnings=[*transport_warnings, *contract_warnings(contract)],
                    attempts=attempt_number,
                    context_version_id=context_version_id,
                    context_content_sha256=context_content_sha256,
                )
                _finish_agent_observation(
                    agent_observation,
                    base_metadata,
                    agent_started,
                    attempt_number,
                    result.validation_status,
                )
                return result

            result = ContractGenerationResult(
                run_id=str(run_id),
                trace_id=trace_id,
                feature_slug=feature_slug,
                validation_status="blocked",
                warnings=[
                    "contract generation blocked after intent validation/repair failure",
                    *transport_warnings,
                ],
                errors=validation_errors,
                attempts=attempt_state["count"],
            )
            _finish_agent_observation(
                agent_observation,
                base_metadata,
                agent_started,
                attempt_state["count"],
                result.validation_status,
            )
            return result

    async def _request_provider(
        self,
        request: StructuredGenerationRequest,
    ) -> Any | ContractValidationIssue:
        started = time.perf_counter()
        start_timing = _request_timing(
            request,
            stage="provider_request",
            elapsed_ms=0,
            provider_status="started",
            model=self._provider.model_name,
        )
        log_timing(logger, "generation_started", **start_timing)
        try:
            response = await self._provider.generate(request)
        except asyncio.CancelledError:
            failed = {
                **start_timing,
                "elapsed_ms": _elapsed_ms(started),
                "provider_status": ProviderFailureCategory.CANCELLED.value,
                "error_type": ProviderFailureCategory.CANCELLED.value,
            }
            log_timing(logger, "generation_failed", **failed)
            raise
        except ProviderError as exc:
            failed = {
                **start_timing,
                "elapsed_ms": _elapsed_ms(started),
                "provider_status": exc.category.value,
                "error_type": exc.category.value,
                "status_code": exc.status_code,
            }
            log_timing(logger, "generation_failed", **failed)
            return ContractValidationIssue(
                code=exc.category.value,
                path="provider",
                message=exc.safe_message,
                status_code=exc.status_code,
                provider_error_code=exc.error_code,
            )
        except Exception:
            category = ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR
            failed = {
                **start_timing,
                "elapsed_ms": _elapsed_ms(started),
                "provider_status": category.value,
                "error_type": category.value,
            }
            log_timing(logger, "generation_failed", **failed)
            return ContractValidationIssue(
                code=category.value,
                path="provider",
                message="structured generation provider failed unexpectedly",
            )

        completed = _request_timing(
            request,
            stage="provider_request",
            elapsed_ms=_elapsed_ms(started),
            response_bytes=len(response.content.encode("utf-8")),
            provider_status="ok",
            model=response.model,
        )
        log_timing(logger, "generation_completed", **completed)
        return response

    @staticmethod
    def _blocked_result(
        run_id: uuid.UUID,
        trace_id: str,
        feature_slug: str,
        *,
        attempts: int,
        issue: ContractValidationIssue,
        warning: str,
    ) -> ContractGenerationResult:
        return ContractGenerationResult(
            run_id=str(run_id),
            trace_id=trace_id,
            feature_slug=feature_slug,
            validation_status="blocked",
            warnings=[warning],
            errors=[issue],
            attempts=attempts,
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
    title = re.sub(
        r"^(?:(?:feature\s+)?spec(?:ification)?)\s*(?:[-—:|]+\s*)?",
        "",
        title,
        flags=re.IGNORECASE,
    )
    slug = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")
    if any(marker in slug for marker in ("api_key", "credential", "password", "secret", "token")):
        return "feature"
    return slug or "feature"


def _parse_candidate(
    candidate: str,
) -> tuple[dict[str, Any] | None, list[ContractValidationIssue]]:
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        incomplete = isinstance(exc, json.JSONDecodeError) and (
            exc.pos >= max(len(candidate.rstrip()) - 1, 0) or "unterminated" in exc.msg.casefold()
        )
        return None, [
            ContractValidationIssue(
                code="incomplete_json" if incomplete else "invalid_json",
                path="$",
                message=(
                    "candidate JSON appears incomplete"
                    if incomplete
                    else f"candidate is not valid JSON at line {exc.lineno} column {exc.colno}"
                ),
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
    return value, []


def _parse_candidate_with_trace(
    candidate: str,
    model: str,
    attempt_number: int,
    tracer: InstrumentationTracer,
    base_metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[ContractValidationIssue]]:
    started = time.perf_counter()
    response_bytes = len(candidate.encode("utf-8"))
    with tracer.observe(
        "JSON_parsing",
        as_type="span",
        metadata={
            **base_metadata,
            **timing_metadata(
                stage="JSON_parsing",
                response_bytes=response_bytes,
                attempt_number=attempt_number,
                model=model,
                provider_status="started",
            ),
        },
    ) as observation:
        value, errors = _parse_candidate(candidate)
        timing = {
            "stage": "JSON_parsing",
            "elapsed_ms": _elapsed_ms(started),
            "response_bytes": response_bytes,
            "intent_bytes": response_bytes,
            "attempt_number": attempt_number,
            "model": model,
            "provider_status": "valid" if value is not None else "invalid",
        }
        log_timing(logger, "stage_completed", **timing)
        observation.update(metadata={**base_metadata, **timing_metadata(**timing)})
        return value, errors


def _validate_intent_value(
    value: dict[str, Any],
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    expected_feature_slug: str,
    context_summary: str | None = None,
    context_evidence_ids: set[str] | None = None,
) -> tuple[ContractIntent | None, list[ContractValidationIssue]]:
    try:
        intent = ContractIntent.model_validate(value)
    except ValidationError as exc:
        return None, _pydantic_issues(exc)
    grounding_errors = validate_intent_grounding(
        intent,
        source_profile,
        feature_spec,
        expected_feature_slug=expected_feature_slug,
        context_summary=context_summary,
        context_evidence_ids=context_evidence_ids,
    )
    if grounding_errors:
        return None, [ContractValidationIssue(**error.as_dict()) for error in grounding_errors]
    return intent, []


def _validate_intent_with_trace(
    value: dict[str, Any],
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    expected_feature_slug: str,
    context_summary: str | None,
    context_evidence_ids: set[str],
    response_bytes: int,
    model: str,
    attempt_number: int,
    tracer: InstrumentationTracer,
    base_metadata: dict[str, Any],
    envelope_metadata: dict[str, Any],
) -> tuple[ContractIntent | None, list[ContractValidationIssue]]:
    started = time.perf_counter()
    with tracer.observe(
        "intent_validation",
        as_type="span",
        metadata={
            **base_metadata,
            **timing_metadata(
                stage="intent_validation",
                response_bytes=response_bytes,
                attempt_number=attempt_number,
                model=model,
                provider_status="started",
                **envelope_metadata,
            ),
        },
    ) as observation:
        intent, errors = _validate_intent_value(
            value,
            source_profile,
            feature_spec,
            expected_feature_slug=expected_feature_slug,
            context_summary=context_summary,
            context_evidence_ids=context_evidence_ids,
        )
        counts = _intent_counts(intent or value)
        timing = {
            "stage": "intent_validation",
            "elapsed_ms": _elapsed_ms(started),
            "response_bytes": response_bytes,
            "intent_bytes": response_bytes,
            "attempt_number": attempt_number,
            "model": model,
            "provider_status": "valid" if intent is not None else "invalid",
            **envelope_metadata,
            **counts,
        }
        log_timing(logger, "stage_completed", **timing)
        observation.update(metadata={**base_metadata, **timing_metadata(**timing)})
        return intent, errors


def _compile_with_trace(
    intent: ContractIntent,
    source_profile: SourceProfile,
    *,
    spec_sha256: str,
    context_version_id: UUID | None,
    context_content_sha256: str | None,
    context_evidence_ids: list[str],
    model: str,
    attempt_number: int,
    tracer: InstrumentationTracer,
    base_metadata: dict[str, Any],
) -> tuple[dict[str, Any], ContractValidationIssue | None]:
    started = time.perf_counter()
    with tracer.observe(
        "contract_compilation",
        as_type="span",
        metadata={
            **base_metadata,
            **timing_metadata(
                stage="contract_compilation",
                attempt_number=attempt_number,
                model=model,
                provider_status="started",
            ),
        },
    ) as observation:
        try:
            payload, metadata = compile_contract_payload(
                intent,
                source_profile,
                spec_sha256=spec_sha256,
                context_version_id=context_version_id,
                context_content_sha256=context_content_sha256,
                context_evidence_ids=context_evidence_ids,
            )
        except Exception:
            timing = {
                "stage": "contract_compilation",
                "elapsed_ms": _elapsed_ms(started),
                "attempt_number": attempt_number,
                "model": model,
                "provider_status": "blocked",
                "error_type": "compiler_error",
            }
            log_timing(logger, "stage_failed", **timing)
            observation.update(metadata={**base_metadata, **timing_metadata(**timing)})
            return {}, ContractValidationIssue(
                code="compiler_error",
                path="compiler",
                message="validated intent could not be deterministically compiled",
            )
        counts = {
            "event_count": metadata.observed_event_count,
            "field_count": metadata.observed_field_count,
            "entity_count": metadata.entity_count,
            "funnel_count": metadata.funnel_count,
            "metric_count": metadata.metric_count,
            "dimension_count": metadata.dimension_count,
        }
        timing = {
            "stage": "contract_compilation",
            "elapsed_ms": _elapsed_ms(started),
            "attempt_number": attempt_number,
            "model": model,
            "provider_status": "completed",
            **counts,
        }
        log_timing(logger, "stage_completed", **timing)
        observation.update(metadata={**base_metadata, **timing_metadata(**timing)})
        return payload, None


def _validate_final_contract_with_trace(
    value: dict[str, Any],
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    spec_sha256: str,
    expected_feature_slug: str,
    model: str,
    attempt_number: int,
    tracer: InstrumentationTracer,
    base_metadata: dict[str, Any],
) -> tuple[AnalyticsContract | None, list[ContractValidationIssue]]:
    started = time.perf_counter()
    with tracer.observe(
        "final_contract_validation",
        as_type="span",
        metadata={
            **base_metadata,
            **timing_metadata(
                stage="final_contract_validation",
                attempt_number=attempt_number,
                model=model,
                provider_status="started",
            ),
        },
    ) as observation:
        contract, errors = _validate_contract_value(
            value,
            source_profile,
            feature_spec,
            spec_sha256=spec_sha256,
            expected_feature_slug=expected_feature_slug,
        )
        timing = {
            "stage": "final_contract_validation",
            "elapsed_ms": _elapsed_ms(started),
            "attempt_number": attempt_number,
            "model": model,
            "provider_status": "valid" if contract is not None else "blocked",
            "event_count": len(value.get("events", [])),
            "field_count": len(value.get("fields", [])),
            "entity_count": len(value.get("entities", [])),
            "funnel_count": len(value.get("funnels", [])),
            "metric_count": len(value.get("metrics", [])),
            "dimension_count": len(value.get("dimensions", [])),
        }
        log_timing(logger, "stage_completed", **timing)
        observation.update(metadata={**base_metadata, **timing_metadata(**timing)})
        return contract, errors


def _validate_contract_value(
    value: dict[str, Any],
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    spec_sha256: str,
    expected_feature_slug: str,
) -> tuple[AnalyticsContract | None, list[ContractValidationIssue]]:
    try:
        contract = AnalyticsContract.model_validate_with_profile(value, source_profile)
    except ValidationError as exc:
        return None, _pydantic_issues(exc, code_prefix="compiler_")
    grounding_errors = validate_contract_grounding(
        contract,
        source_profile,
        feature_spec,
        spec_sha256=spec_sha256,
        expected_feature_slug=expected_feature_slug,
    )
    if grounding_errors:
        return None, [
            ContractValidationIssue(
                code=f"compiler_{error.code}",
                path=error.path,
                message=error.message,
            )
            for error in grounding_errors
        ]
    return contract, []


def _pydantic_issues(
    error: ValidationError,
    *,
    code_prefix: str = "",
) -> list[ContractValidationIssue]:
    return [
        ContractValidationIssue(
            code=f"{code_prefix}{item['type']}",
            path=".".join(str(part) for part in item["loc"]) or "$",
            message=item["msg"],
        )
        for item in error.errors(include_input=False, include_url=False)
    ]


def _declared_entity_ids(value: dict[str, Any] | None) -> list[str]:
    if value is None or not isinstance(value.get("entities"), list):
        return []
    return sorted(
        {
            item["id"]
            for item in value["entities"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
    )


def _build_repair_scope(
    value: dict[str, Any] | None,
    errors: list[ContractValidationIssue],
) -> dict[str, Any]:
    must_correct = list(dict.fromkeys(item.path for item in errors))
    must_preserve: dict[str, Any] = {}
    if value is None or "$" in must_correct:
        return {"must_preserve": must_preserve, "must_correct": must_correct}

    for field_name in _REPAIR_ARRAY_FIELDS:
        field_value = value.get(field_name)
        if not isinstance(field_value, list):
            continue
        implicated_indices = {
            index
            for path in must_correct
            if (index := _array_index_from_path(path, field_name)) is not None
        }
        for index, item in enumerate(field_value):
            if index not in implicated_indices:
                must_preserve[f"{field_name}.{index}"] = item

    for field_name in _REPAIR_SCALAR_FIELDS:
        if field_name not in value:
            continue
        if not any(_path_is_within(path, field_name) for path in must_correct):
            must_preserve[field_name] = value[field_name]

    return {"must_preserve": must_preserve, "must_correct": must_correct}


def _build_deterministic_repair_hints(
    errors: list[ContractValidationIssue],
    source_profile: SourceProfile,
    feature_spec: str,
) -> list[dict[str, Any]]:
    error_codes = {item.code for item in errors}
    requirements = semantic_contract_requirements(feature_spec, source_profile)
    hints: list[dict[str, Any]] = []
    if "missing_conversion_metric" in error_codes and len(requirements.ordered_event_names) >= 2:
        start_event = requirements.ordered_event_names[0]
        end_event = requirements.ordered_event_names[-1]
        hints.append(
            {
                "error_code": "missing_conversion_metric",
                "action": "add_array_element",
                "path": "metrics",
                "required_value_type": "ratio",
                "required_numerator_reference": end_event,
                "grounded_numerator_example": f"count({end_event})",
                "required_denominator_reference": start_event,
                "grounded_denominator_example": f"count({start_event})",
            }
        )
    if (
        "missing_requested_duration_metric" in error_codes
        or "ungrounded_duration_metric" in error_codes
    ):
        hint: dict[str, Any] = {
            "error_code": "duration_metric_grounding",
            "action": (
                "add_array_element"
                if "missing_requested_duration_metric" in error_codes
                else "correct_implicated_element"
            ),
            "path": "metrics",
            "required_value_type": "duration",
            "allowed_duration_fields": list(requirements.duration_field_paths),
            "grounded_numerator_examples": [
                f"avg({field})" for field in requirements.duration_field_paths
            ],
        }
        if len(requirements.ordered_event_names) >= 2:
            hint["grounded_start_event"] = requirements.ordered_event_names[0]
            hint["grounded_end_event"] = requirements.ordered_event_names[-1]
        hints.append(hint)
    if (
        "missing_requested_failure_metric" in error_codes
        or "missing_requested_failure_predicate" in error_codes
        or "ungrounded_failure_metric" in error_codes
    ):
        fields = {item.path: item for item in source_profile.fields}
        hints.append(
            {
                "error_code": "failure_metric_grounding",
                "action": (
                    "add_array_element"
                    if "missing_requested_failure_metric" in error_codes
                    else "correct_implicated_element"
                ),
                "path": "metrics",
                "allowed_false_predicates": [
                    f"{field} = false" for field in requirements.boolean_predicate_fields
                ],
                "predicate_event_scopes": {
                    field: fields[field].observed_in_events
                    for field in requirements.boolean_predicate_fields
                    if field in fields
                },
            }
        )
    if "ungrounded_ratio_operand" in error_codes:
        hints.append(
            {
                "error_code": "ungrounded_ratio_operand",
                "action": "correct_exact_error_paths",
                "paths": [item.path for item in errors if item.code == "ungrounded_ratio_operand"],
                "allowed_observed_events": sorted(source_profile.event_names),
                "allowed_observed_fields": sorted(source_profile.field_paths),
            }
        )
    return hints


def _repair_preservation_errors(
    previous: dict[str, Any] | None,
    repaired: dict[str, Any],
    repair_scope: dict[str, Any],
) -> list[ContractValidationIssue]:
    must_preserve = repair_scope.get("must_preserve", {})
    must_correct = repair_scope.get("must_correct", [])
    if not isinstance(must_preserve, dict) or not isinstance(must_correct, list):
        return []

    changed_paths: set[str] = set()
    for path, expected in must_preserve.items():
        found, actual = _value_at_path(repaired, path)
        if not found or _stable_json_value(actual) != _stable_json_value(expected):
            changed_paths.add(path)

    if previous is not None:
        for field_name in _REPAIR_ARRAY_FIELDS:
            previous_array = previous.get(field_name)
            repaired_array = repaired.get(field_name)
            if not isinstance(previous_array, list):
                continue
            array_addition_allowed = field_name in must_correct
            if not isinstance(repaired_array, list):
                if not _path_is_explicitly_correctable(field_name, must_correct):
                    changed_paths.add(field_name)
                continue
            if not array_addition_allowed and len(repaired_array) > len(previous_array):
                changed_paths.update(
                    f"{field_name}.{index}"
                    for index in range(len(previous_array), len(repaired_array))
                )

    return [
        ContractValidationIssue(
            code="repair_modified_unrelated_field",
            path=path,
            message="repair modified a JSON value outside the authorized repair scope",
        )
        for path in sorted(changed_paths)
    ]


def _array_index_from_path(path: str, field_name: str) -> int | None:
    path = path.removeprefix("$.")
    match = re.match(rf"^{re.escape(field_name)}(?:\.|\[)(\d+)(?:\]|\.|$)", path)
    return int(match.group(1)) if match is not None else None


def _path_is_within(path: str, field_name: str) -> bool:
    path = path.removeprefix("$.")
    return (
        path == field_name or path.startswith(f"{field_name}.") or path.startswith(f"{field_name}[")
    )


def _path_is_explicitly_correctable(path: str, must_correct: list[Any]) -> bool:
    return any(isinstance(item, str) and _path_is_within(item, path) for item in must_correct)


def _value_at_path(value: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = value
    for token in path.split("."):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False, None
    return True, current


def _stable_json_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalized_candidate_hash(value: dict[str, Any]) -> str:
    normalized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalized_error_signature(errors: list[ContractValidationIssue]) -> str:
    normalized = json.dumps(
        sorted((item.code, item.path) for item in errors),
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _intent_counts(value: ContractIntent | dict[str, Any]) -> dict[str, int]:
    if isinstance(value, ContractIntent):
        return {
            "entity_count": len(value.entities),
            "funnel_count": len(value.funnels),
            "metric_count": len(value.metrics),
            "dimension_count": len(value.dimensions),
        }
    return {
        key: len(value.get(source, [])) if isinstance(value.get(source), list) else 0
        for key, source in (
            ("entity_count", "entities"),
            ("funnel_count", "funnels"),
            ("metric_count", "metrics"),
            ("dimension_count", "dimensions"),
        )
    }


def _request_timing(
    request: StructuredGenerationRequest,
    *,
    stage: str,
    elapsed_ms: int,
    provider_status: str,
    response_bytes: int | None = None,
    error_type: str | None = None,
    status_code: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    measurements = request.measurements
    return {
        "stage": stage,
        "elapsed_ms": elapsed_ms,
        "prompt_bytes": measurements.prompt_bytes if measurements else None,
        "estimated_prompt_tokens": (measurements.estimated_prompt_tokens if measurements else None),
        "json_schema_bytes": measurements.json_schema_bytes if measurements else None,
        "profile_summary_bytes": (measurements.profile_summary_bytes if measurements else None),
        "response_bytes": response_bytes,
        "attempt_number": request.attempt_number,
        "model": model,
        "provider_status": provider_status,
        "error_type": error_type,
        "status_code": status_code,
    }


def _finish_agent_observation(
    observation: Any,
    base_metadata: dict[str, Any],
    started: float,
    attempt_number: int,
    status: str,
) -> None:
    observation.update(
        metadata={
            **base_metadata,
            **timing_metadata(
                stage="instrumentation_agent",
                elapsed_ms=_elapsed_ms(started),
                attempt_number=attempt_number,
                model=base_metadata["model"],
                provider_status=status,
            ),
        }
    )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
