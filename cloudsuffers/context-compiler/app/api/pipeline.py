from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, status
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.agents.analytics import AnalyticsResult
from app.agents.instrumentation import feature_slug_from_spec
from app.agents.schema_planner import SchemaVersionRecord
from app.contracts.models import AnalyticsContract
from app.core.logging import get_logger
from app.profiling.models import StrictModel

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
logger = get_logger(__name__)

_MAX_SPEC_BYTES = 5 * 1024 * 1024


class PipelineRunResponse(StrictModel):
    run_id: str
    feature_slug: str
    status: str
    contract: AnalyticsContract | None = None
    schema_plan: dict | None = None
    context_version_id: str | None = None
    insights: list[dict] | None = Field(default=None)
    errors: list[str] = Field(default_factory=list)
    duration_ms: int


class PipelineErrorDetail(StrictModel):
    code: str
    message: str


class PipelineErrorResponse(StrictModel):
    detail: PipelineErrorDetail


@router.post(
    "/run",
    response_model=PipelineRunResponse,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": PipelineErrorResponse}},
)
async def run_pipeline(
    request: Request,
    spec: UploadFile,
    events: UploadFile,
    dry_run: bool = Form(default=False),
) -> PipelineRunResponse:
    settings = request.app.state.settings
    profiler = request.app.state.source_profiler
    agent = request.app.state.instrumentation_agent
    context_repository = request.app.state.context_repository
    schema_planner = request.app.state.schema_planner
    context_agent = request.app.state.context_agent
    analytics_agent = request.app.state.analytics_agent

    started_wall = datetime.now(UTC)
    started = time.perf_counter()
    run_id = uuid.uuid4()
    temporary_path: Path | None = None
    errors: list[str] = []
    feature_slug = "feature"
    try:
        _validate_filename(spec, {".md", ".markdown"}, "spec")
        _validate_filename(events, {".ndjson"}, "events")

        spec_bytes = await _read_bounded(spec, _MAX_SPEC_BYTES, 64 * 1024, "spec")
        if not spec_bytes:
            _unprocessable("empty_spec", "specification file must not be empty")
        try:
            feature_spec = spec_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _unprocessable("invalid_spec_encoding", "specification must be valid UTF-8 Markdown")
        if not feature_spec.strip():
            _unprocessable("empty_spec", "specification file must contain non-whitespace text")

        maximum_events_size = settings.profile_max_upload_bytes
        if events.size is not None and events.size > maximum_events_size:
            _unprocessable(
                "file_too_large",
                f"events file exceeds the {maximum_events_size}-byte limit",
            )
        uploaded_bytes = 0
        with NamedTemporaryFile(mode="wb", suffix=".ndjson", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await events.read(settings.profile_upload_chunk_bytes):
                uploaded_bytes += len(chunk)
                if uploaded_bytes > maximum_events_size:
                    _unprocessable(
                        "file_too_large",
                        f"events file exceeds the {maximum_events_size}-byte limit",
                    )
                temporary.write(chunk)

        feature_slug = feature_slug_from_spec(feature_spec)

        # 1. Profile events
        source_profile = await run_in_threadpool(profiler.profile, temporary_path)
        if source_profile.file.valid_row_count == 0:
            _unprocessable("no_valid_rows", "events file contains no valid JSON object rows")

        # 2. Context lookup
        approved_context = None
        try:
            approved_context = await run_in_threadpool(context_repository.latest_approved)
        except Exception:
            logger.warning("pipeline_context_lookup_failed")
            approved_context = None

        if approved_context is None:
            errors.append("no approved context version is available")
            return _finalize_response(
                run_id=run_id,
                feature_slug=feature_slug,
                status_value="contract_blocked",
                contract=None,
                schema_plan=None,
                context_version_id=None,
                insights=None,
                errors=errors,
                started=started,
                started_wall=started_wall,
                request=request,
            )

        # 3. Generate contract
        contract_result = await agent.generate_contract(
            feature_spec,
            source_profile,
            context_summary=approved_context.compact_json(),
            context_version_id=approved_context.context_version_id,
            context_content_sha256=approved_context.content_sha256,
            context_evidence_ids=approved_context.evidence_ids,
            run_id=run_id,
        )
        if (
            contract_result.validation_status != "valid"
            or contract_result.analytics_contract is None
        ):
            errors.extend(item.message for item in contract_result.errors)
            errors.extend(contract_result.warnings)
            return _finalize_response(
                run_id=run_id,
                feature_slug=contract_result.feature_slug,
                status_value="contract_blocked",
                contract=None,
                schema_plan=None,
                context_version_id=str(approved_context.context_version_id),
                insights=None,
                errors=errors,
                started=started,
                started_wall=started_wall,
                request=request,
            )

        contract = contract_result.analytics_contract
        feature_slug = contract.feature.slug

        # 4. Plan (and optionally deploy) schema
        schema_record: SchemaVersionRecord | None = None
        try:
            schema_record = await run_in_threadpool(
                schema_planner.plan, contract, run_id, dry_run=dry_run
            )
        except Exception as exc:
            logger.warning(
                "pipeline_schema_plan_failed",
                extra={"error_type": type(exc).__name__},
            )
            errors.append("schema planning failed")
            return _finalize_response(
                run_id=run_id,
                feature_slug=feature_slug,
                status_value="error",
                contract=contract,
                schema_plan=None,
                context_version_id=str(approved_context.context_version_id),
                insights=None,
                errors=errors,
                started=started,
                started_wall=started_wall,
                request=request,
            )

        # 5. Persist schema version
        try:
            await run_in_threadpool(
                schema_planner.persist,
                schema_record,
                settings.clickhouse_metadata_database,
            )
        except Exception as exc:
            logger.warning(
                "pipeline_schema_persist_failed",
                extra={"error_type": type(exc).__name__},
            )
            errors.append("schema version could not be persisted")

        # 6. Update context
        updated_context = approved_context
        try:
            updated_context = await run_in_threadpool(
                context_agent.update_after_schema,
                schema_record,
                contract,
                approved_context,
                run_id,
            )
        except Exception as exc:
            logger.warning(
                "pipeline_context_update_failed",
                extra={"error_type": type(exc).__name__},
            )
            errors.append("context version could not be updated")

        # 7. Run analytics
        analytics_result: AnalyticsResult | None = None
        try:
            analytics_result = await analytics_agent.run(contract, updated_context, run_id)
        except Exception as exc:
            logger.warning(
                "pipeline_analytics_failed",
                extra={"error_type": type(exc).__name__},
            )
            errors.append("analytics agent failed")

        # 8. Persist evidence + insights
        if analytics_result is not None:
            try:
                await run_in_threadpool(
                    analytics_agent.persist_evidence,
                    analytics_result,
                    updated_context.context_version_id,
                )
            except Exception as exc:
                logger.warning(
                    "pipeline_analytics_persist_failed",
                    extra={"error_type": type(exc).__name__},
                )
                errors.append("analytics evidence could not be persisted")

        # 9. Record pipeline_runs
        try:
            await run_in_threadpool(
                _persist_pipeline_run,
                request=request,
                run_id=run_id,
                feature_slug=feature_slug,
                status_value="completed",
                contract=contract,
                schema_record=schema_record,
                context_version_id=updated_context.context_version_id,
                started_wall=started_wall,
                errors=errors,
            )
        except Exception as exc:
            logger.warning(
                "pipeline_run_persist_failed",
                extra={"error_type": type(exc).__name__},
            )

        insights_payload: list[dict] | None = None
        if analytics_result is not None:
            insights_payload = [
                {
                    "title": insight.title,
                    "summary": insight.summary,
                    "confidence": insight.confidence,
                    "category": insight.category,
                }
                for insight in analytics_result.insights
            ]

        schema_plan_payload = {
            "strategy": schema_record.strategy_name,
            "table_name": schema_record.table_name,
            "ddl": schema_record.ddl,
            "deployed": schema_record.deployed_at is not None,
        }

        return _finalize_response(
            run_id=run_id,
            feature_slug=feature_slug,
            status_value="completed",
            contract=contract,
            schema_plan=schema_plan_payload,
            context_version_id=str(updated_context.context_version_id),
            insights=insights_payload,
            errors=errors,
            started=started,
            started_wall=started_wall,
            request=request,
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        await spec.close()
        await events.close()


def _finalize_response(
    *,
    run_id: uuid.UUID,
    feature_slug: str,
    status_value: str,
    contract: AnalyticsContract | None,
    schema_plan: dict | None,
    context_version_id: str | None,
    insights: list[dict] | None,
    errors: list[str],
    started: float,
    started_wall: datetime,
    request: Request,
) -> PipelineRunResponse:
    duration_ms = round((time.perf_counter() - started) * 1000)
    if status_value != "completed":
        try:
            _persist_pipeline_run(
                request=request,
                run_id=run_id,
                feature_slug=feature_slug,
                status_value=status_value,
                contract=contract,
                schema_record=None,
                context_version_id=(uuid.UUID(context_version_id) if context_version_id else None),
                started_wall=started_wall,
                errors=errors,
            )
        except Exception:
            logger.warning("pipeline_run_persist_failed_final")
    return PipelineRunResponse(
        run_id=str(run_id),
        feature_slug=feature_slug,
        status=status_value,
        contract=contract,
        schema_plan=schema_plan,
        context_version_id=context_version_id,
        insights=insights,
        errors=errors,
        duration_ms=duration_ms,
    )


def _persist_pipeline_run(
    *,
    request: Request,
    run_id: uuid.UUID,
    feature_slug: str,
    status_value: str,
    contract: AnalyticsContract | None,
    schema_record: SchemaVersionRecord | None,
    context_version_id: uuid.UUID | None,
    started_wall: datetime,
    errors: list[str],
) -> None:
    settings = request.app.state.settings
    context_repository = request.app.state.context_repository
    client = context_repository._get_client()  # noqa: SLF001 - shared metadata client
    table = f"`{settings.clickhouse_metadata_database}`.`pipeline_runs`"
    now = datetime.now(UTC)
    contract_id = None
    if schema_record is not None:
        contract_id = schema_record.contract_id
    error_message = json.dumps(errors, sort_keys=True) if errors else None
    events_sha256 = contract.source.events_sha256 if contract is not None else None
    spec_sha256 = contract.source.spec_sha256 if contract is not None else None
    client.insert(
        table,
        [
            [
                run_id,
                feature_slug,
                status_value,
                spec_sha256,
                events_sha256,
                contract_id,
                schema_record.schema_version_id if schema_record is not None else None,
                context_version_id,
                None,
                error_message,
                started_wall,
                now,
                now,
            ]
        ],
        column_names=[
            "run_id",
            "feature_slug",
            "status",
            "spec_sha256",
            "events_sha256",
            "contract_id",
            "schema_version_id",
            "context_version_id",
            "langfuse_trace_id",
            "error_message",
            "started_at",
            "completed_at",
            "updated_at",
        ],
    )


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


def _unprocessable(code: str, message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message},
    )
