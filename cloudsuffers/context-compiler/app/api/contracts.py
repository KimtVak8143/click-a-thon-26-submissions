import asyncio
import time
import uuid
from collections.abc import Coroutine
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.agents.instrumentation import (
    ContractGenerationResult,
    ContractValidationIssue,
    InstrumentationAgent,
    feature_slug_from_spec,
)
from app.api.dependencies import (
    get_context_repository,
    get_instrumentation_agent,
    get_source_profiler,
)
from app.context.repository import ContextRepositoryProtocol
from app.contracts.models import AnalyticsContract
from app.contracts.prompts import PROMPT_VERSION
from app.core.logging import get_logger
from app.core.timing import log_timing, timing_metadata
from app.core.tracing import SafeLangfuseInstrumentationTracer
from app.profiling.models import SourceProfile, StrictModel
from app.profiling.profiler import SourceProfiler

router = APIRouter(prefix="/contracts", tags=["contracts"])
logger = get_logger(__name__)


class ContractGenerateResponse(StrictModel):
    run_id: str
    source_profile: SourceProfile
    analytics_contract: AnalyticsContract | None = None
    validation_status: Literal["valid", "blocked"]
    warnings: list[str] = Field(default_factory=list)
    errors: list[ContractValidationIssue] = Field(default_factory=list)
    trace_id: str
    attempts: int
    context_version_id: uuid.UUID | None = None
    context_content_sha256: str | None = None


class ContractGenerateErrorDetail(StrictModel):
    code: str
    message: str


class ContractGenerateErrorResponse(StrictModel):
    detail: ContractGenerateErrorDetail


@router.post(
    "/generate",
    response_model=ContractGenerateResponse,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ContractGenerateErrorResponse}},
)
async def generate_contract(
    request: Request,
    spec: UploadFile,
    events: UploadFile,
    profiler: Annotated[SourceProfiler, Depends(get_source_profiler)],
    agent: Annotated[InstrumentationAgent, Depends(get_instrumentation_agent)],
    context_repository: Annotated[ContextRepositoryProtocol, Depends(get_context_repository)],
) -> ContractGenerateResponse:
    settings = request.app.state.settings
    temporary_path: Path | None = None
    run_id = uuid.uuid4()
    langfuse_state = getattr(request.app.state, "langfuse", None)
    langfuse_client = langfuse_state.client if langfuse_state is not None else None
    provider = request.app.state.llm_provider
    tracer = SafeLangfuseInstrumentationTracer(langfuse_client, run_id.hex)
    api_trace_metadata = {
        "run_id": str(run_id),
        "model": provider.model_name,
        "prompt_version": PROMPT_VERSION,
    }
    try:
        upload_started = time.perf_counter()
        with tracer.observe(
            "upload_validation",
            as_type="span",
            metadata={
                **api_trace_metadata,
                "stage": "upload_validation",
                "attempt_number": 1,
                "model": provider.model_name,
                "provider_status": "started",
            },
        ) as upload_observation:
            _validate_filename(spec, {".md", ".markdown"}, "spec")
            _validate_filename(events, {".ndjson"}, "events")

            spec_bytes = await _read_bounded(
                spec,
                settings.contract_spec_max_upload_bytes,
                settings.profile_upload_chunk_bytes,
                "spec",
            )
            if not spec_bytes:
                _unprocessable("empty_spec", "specification file must not be empty")
            try:
                feature_spec = spec_bytes.decode("utf-8")
            except UnicodeDecodeError:
                _unprocessable(
                    "invalid_spec_encoding", "specification must be valid UTF-8 Markdown"
                )
            if not feature_spec.strip():
                _unprocessable("empty_spec", "specification file must contain non-whitespace text")

            maximum_size = settings.profile_max_upload_bytes
            if events.size is not None and events.size > maximum_size:
                _unprocessable(
                    "file_too_large", f"events file exceeds the {maximum_size}-byte limit"
                )
            uploaded_bytes = 0
            with NamedTemporaryFile(mode="wb", suffix=".ndjson", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                while chunk := await events.read(settings.profile_upload_chunk_bytes):
                    uploaded_bytes += len(chunk)
                    if uploaded_bytes > maximum_size:
                        _unprocessable(
                            "file_too_large",
                            f"events file exceeds the {maximum_size}-byte limit",
                        )
                    temporary.write(chunk)
            upload_timing = {
                "stage": "upload_validation",
                "elapsed_ms": round((time.perf_counter() - upload_started) * 1000),
                "attempt_number": 1,
                "model": provider.model_name,
                "provider_status": "completed",
            }
            log_timing(logger, "stage_completed", **upload_timing)
            upload_observation.update(
                metadata={
                    **api_trace_metadata,
                    "feature_slug": feature_slug_from_spec(feature_spec),
                    **timing_metadata(**upload_timing),
                }
            )

        profiling_started = time.perf_counter()
        with tracer.observe(
            "source_profiling",
            as_type="span",
            metadata={
                **api_trace_metadata,
                "feature_slug": feature_slug_from_spec(feature_spec),
                "stage": "source_profiling",
                "attempt_number": 1,
                "model": provider.model_name,
                "provider_status": "started",
            },
        ) as profiling_observation:
            source_profile = await run_in_threadpool(profiler.profile, temporary_path)
            _validate_profile(source_profile)
            profiling_timing = {
                "stage": "source_profiling",
                "elapsed_ms": round((time.perf_counter() - profiling_started) * 1000),
                "attempt_number": 1,
                "model": provider.model_name,
                "provider_status": "completed",
            }
            log_timing(logger, "stage_completed", **profiling_timing)
            profiling_observation.update(
                metadata={
                    **api_trace_metadata,
                    "feature_slug": feature_slug_from_spec(feature_spec),
                    "source_checksum": source_profile.file.sha256,
                    **timing_metadata(**profiling_timing),
                }
            )

        context_started = time.perf_counter()
        with tracer.observe(
            "context_resolution",
            as_type="span",
            metadata={
                **api_trace_metadata,
                "stage": "context_resolution",
                "attempt_number": 1,
                "model": provider.model_name,
                "provider_status": "started",
            },
        ) as context_observation:
            context_error = False
            try:
                approved_context = await run_in_threadpool(context_repository.latest_approved)
            except Exception:
                approved_context = None
                context_error = True
                logger.warning(
                    "context_resolution_failed",
                    extra=timing_metadata(
                        stage="context_resolution",
                        attempt_number=1,
                        model=provider.model_name,
                        provider_status="unavailable",
                        error_type="context_unavailable",
                    ),
                )
            context_timing = {
                "stage": "context_resolution",
                "elapsed_ms": round((time.perf_counter() - context_started) * 1000),
                "attempt_number": 1,
                "model": provider.model_name,
                "provider_status": (
                    "approved"
                    if approved_context
                    else "unavailable"
                    if context_error
                    else "missing"
                ),
            }
            log_timing(logger, "stage_completed", **context_timing)
            context_observation.update(
                metadata={
                    **api_trace_metadata,
                    **timing_metadata(**context_timing),
                    "context_version_id": (
                        str(approved_context.context_version_id)
                        if approved_context is not None
                        else None
                    ),
                    "context_content_sha256": (
                        approved_context.content_sha256 if approved_context is not None else None
                    ),
                }
            )

        if approved_context is None:
            error_code = "context_unavailable" if context_error else "missing_approved_context"
            error_message = (
                "approved context lookup is temporarily unavailable"
                if context_error
                else "no approved context version is available"
            )
            result = ContractGenerationResult(
                run_id=str(run_id),
                trace_id=run_id.hex,
                feature_slug=feature_slug_from_spec(feature_spec),
                validation_status="blocked",
                warnings=["contract generation requires an approved base context"],
                errors=[
                    ContractValidationIssue(
                        code=error_code,
                        path="context",
                        message=error_message,
                    )
                ],
                attempts=0,
            )
        else:
            result = await _generate_until_disconnect(
                request,
                agent.generate_contract(
                    feature_spec,
                    source_profile,
                    context_summary=approved_context.compact_json(),
                    context_version_id=approved_context.context_version_id,
                    context_content_sha256=approved_context.content_sha256,
                    context_evidence_ids=approved_context.evidence_ids,
                    tracer=tracer,
                    run_id=run_id,
                ),
            )

        warnings = list(result.warnings)
        if langfuse_state is None or langfuse_state.status != "configured":
            warnings.append("Langfuse tracing unavailable; generation continued safely")
        return ContractGenerateResponse(
            run_id=result.run_id,
            source_profile=source_profile,
            analytics_contract=result.analytics_contract,
            validation_status=result.validation_status,
            warnings=warnings,
            errors=result.errors,
            trace_id=result.trace_id,
            attempts=result.attempts,
            context_version_id=(
                approved_context.context_version_id if approved_context is not None else None
            ),
            context_content_sha256=(
                approved_context.content_sha256 if approved_context is not None else None
            ),
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        await spec.close()
        await events.close()


async def _read_bounded(
    upload: UploadFile,
    maximum_size: int,
    chunk_size: int,
    label: str,
) -> bytes:
    if upload.size is not None and upload.size > maximum_size:
        _unprocessable("file_too_large", f"{label} file exceeds the {maximum_size}-byte limit")
    chunks = []
    total = 0
    while chunk := await upload.read(chunk_size):
        total += len(chunk)
        if total > maximum_size:
            _unprocessable("file_too_large", f"{label} file exceeds the {maximum_size}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_filename(upload: UploadFile, suffixes: set[str], label: str) -> None:
    filename = upload.filename
    if (
        not filename
        or "\\" in filename
        or Path(filename).name != filename
        or Path(filename).suffix.lower() not in suffixes
    ):
        allowed = " or ".join(sorted(suffixes))
        _unprocessable("invalid_filename", f"{label} must have a safe {allowed} filename")


def _validate_profile(profile: SourceProfile) -> None:
    if profile.file.valid_row_count == 0:
        _unprocessable("no_valid_rows", "events file contains no valid JSON object rows")
    if profile.file.malformed_row_count:
        _unprocessable(
            "malformed_ndjson",
            f"events file contains {profile.file.malformed_row_count} malformed row(s)",
        )
    if profile.event_profile.unknown_or_missing_event_name_count:
        _unprocessable(
            "missing_event_names",
            "every event row must contain a non-empty event name",
        )
    if profile.time_coverage.minimum is None or profile.time_coverage.maximum is None:
        _unprocessable(
            "missing_event_timestamps",
            "events must contain at least one valid timestamp",
        )


def _unprocessable(code: str, message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message},
    )


async def _generate_until_disconnect(
    request: Request,
    generation: Coroutine[Any, Any, ContractGenerationResult],
) -> ContractGenerationResult:
    generation_task = asyncio.create_task(generation)
    try:
        while True:
            done, _ = await asyncio.wait({generation_task}, timeout=0.1)
            if generation_task in done:
                return generation_task.result()
            if await request.is_disconnected():
                generation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await generation_task
                raise asyncio.CancelledError
    except asyncio.CancelledError:
        generation_task.cancel()
        with suppress(asyncio.CancelledError):
            await generation_task
        raise
