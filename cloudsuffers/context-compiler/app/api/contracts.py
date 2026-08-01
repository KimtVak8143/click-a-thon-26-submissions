import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.agents.instrumentation import ContractValidationIssue, InstrumentationAgent
from app.api.dependencies import get_instrumentation_agent, get_source_profiler
from app.contracts.models import AnalyticsContract
from app.core.tracing import SafeLangfuseInstrumentationTracer
from app.profiling.models import SourceProfile, StrictModel
from app.profiling.profiler import SourceProfiler

router = APIRouter(prefix="/contracts", tags=["contracts"])


class ContractGenerateResponse(StrictModel):
    run_id: str
    source_profile: SourceProfile
    analytics_contract: AnalyticsContract | None = None
    validation_status: Literal["valid", "blocked"]
    warnings: list[str] = Field(default_factory=list)
    errors: list[ContractValidationIssue] = Field(default_factory=list)
    trace_id: str
    attempts: int


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
) -> ContractGenerateResponse:
    settings = request.app.state.settings
    temporary_path: Path | None = None
    try:
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
            _unprocessable("invalid_spec_encoding", "specification must be valid UTF-8 Markdown")
        if not feature_spec.strip():
            _unprocessable("empty_spec", "specification file must contain non-whitespace text")

        maximum_size = settings.profile_max_upload_bytes
        if events.size is not None and events.size > maximum_size:
            _unprocessable("file_too_large", f"events file exceeds the {maximum_size}-byte limit")
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

        source_profile = await run_in_threadpool(profiler.profile, temporary_path)
        _validate_profile(source_profile)

        langfuse_state = getattr(request.app.state, "langfuse", None)
        langfuse_client = langfuse_state.client if langfuse_state is not None else None
        run_id = uuid.uuid4()
        tracer = SafeLangfuseInstrumentationTracer(langfuse_client, run_id.hex)
        result = await agent.generate_contract(
            feature_spec,
            source_profile,
            tracer=tracer,
            run_id=run_id,
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
        )
    finally:
        await spec.close()
        await events.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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
