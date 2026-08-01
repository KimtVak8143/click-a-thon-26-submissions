from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, status
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.agents.analytics import (
    INSIGHTS_PROMPT_NAME,
    INSIGHTS_PROMPT_VERSION,
    AnalyticsResult,
    FeatureInsight,
)
from app.agents.instrumentation import feature_slug_from_spec
from app.agents.schema_planner import SchemaVersionRecord
from app.context.models import ApprovedContext
from app.contracts.models import AnalyticsContract
from app.core.logging import get_logger
from app.core.tracing import SafeLangfuseInstrumentationTracer, TraceObservation
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
    baseline_metrics_service = request.app.state.baseline_metrics

    started_wall = datetime.now(UTC)
    started = time.perf_counter()
    run_id = uuid.uuid4()
    langfuse_state = getattr(request.app.state, "langfuse", None)
    langfuse_client = langfuse_state.client if langfuse_state is not None else None
    tracer = SafeLangfuseInstrumentationTracer(
        langfuse_client,
        run_id.hex,
        feature_name="pipeline",
        tags=["api", "pipeline"],
    )
    trace_context = tracer.observe(
        "run-pipeline",
        as_type="chain",
        input={
            "spec_format": "markdown",
            "events_format": "ndjson",
            "dry_run": dry_run,
        },
        metadata={"run_id": str(run_id), "route": "/pipeline/run"},
    )
    pipeline_observation = trace_context.__enter__()
    trace_error: tuple[type[BaseException] | None, BaseException | None, object | None] = (
        None,
        None,
        None,
    )
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
        with tracer.observe(
            "profile-source",
            as_type="span",
            input={"uploaded_bytes": uploaded_bytes},
        ) as profile_observation:
            source_profile = await run_in_threadpool(profiler.profile, temporary_path)
            if source_profile.file.valid_row_count == 0:
                _unprocessable("no_valid_rows", "events file contains no valid JSON object rows")
            profile_observation.update(
                output={
                    "valid_rows": source_profile.file.valid_row_count,
                    "malformed_rows": source_profile.file.malformed_row_count,
                    "empty_rows": source_profile.file.empty_line_count,
                }
            )

        # 2. Context lookup
        approved_context = None
        with tracer.observe(
            "retrieve-approved-context",
            as_type="retriever",
            input={"feature_slug": feature_slug},
        ) as context_lookup_observation:
            try:
                approved_context = await run_in_threadpool(context_repository.latest_approved)
            except Exception:
                logger.warning("pipeline_context_lookup_failed")
                approved_context = None
            context_lookup_observation.update(
                output={
                    "found": approved_context is not None,
                    "context_version_id": (
                        str(approved_context.context_version_id) if approved_context else None
                    ),
                }
            )

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
                trace_observation=pipeline_observation,
            )

        baseline_snapshot = None
        with tracer.observe(
            "retrieve-baseline-metrics",
            as_type="retriever",
            input={"source_database": settings.clickhouse_database},
        ) as baseline_observation:
            try:
                baseline_snapshot = await run_in_threadpool(baseline_metrics_service.precompute)
            except Exception:
                logger.warning("pipeline_baseline_metrics_lookup_failed")
            baseline_observation.update(
                output={
                    "found": baseline_snapshot is not None,
                    "snapshot_id": (
                        str(baseline_snapshot.snapshot_id) if baseline_snapshot else None
                    ),
                    "metric_count": (len(baseline_snapshot.metrics) if baseline_snapshot else 0),
                }
            )

        context_payload = json.loads(approved_context.compact_json())
        context_evidence_ids = list(approved_context.evidence_ids)
        if baseline_snapshot is not None:
            context_payload["baseline_metrics"] = baseline_snapshot.compact()
            context_evidence_ids.extend(baseline_snapshot.evidence_ids)

        # 3. Generate contract
        contract_result = await agent.generate_contract(
            feature_spec,
            source_profile,
            context_summary=json.dumps(context_payload, sort_keys=True, default=str),
            context_version_id=approved_context.context_version_id,
            context_content_sha256=approved_context.content_sha256,
            context_evidence_ids=sorted(set(context_evidence_ids)),
            tracer=tracer,
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
                trace_observation=pipeline_observation,
            )

        contract = contract_result.analytics_contract
        feature_slug = contract.feature.slug

        # 4. Plan (and optionally deploy) schema
        schema_record: SchemaVersionRecord | None = None
        with tracer.observe(
            "plan-schema",
            as_type="span",
            input={"feature_slug": feature_slug, "dry_run": dry_run},
        ) as schema_observation:
            try:
                schema_record = await run_in_threadpool(
                    schema_planner.plan, contract, run_id, dry_run=dry_run
                )
            except Exception as exc:
                logger.warning(
                    "pipeline_schema_plan_failed",
                    extra={"error_type": type(exc).__name__},
                )
                schema_observation.update(level="ERROR", status_message=type(exc).__name__)
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
                    trace_observation=pipeline_observation,
                )
            schema_observation.update(
                output={
                    "strategy": schema_record.strategy_name,
                    "table_name": schema_record.table_name,
                    "deployed": schema_record.deployed_at is not None,
                }
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
        with tracer.observe(
            "update-context",
            as_type="agent",
            input={
                "feature_slug": feature_slug,
                "base_context_version_id": str(approved_context.context_version_id),
            },
        ) as context_observation:
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
                context_observation.update(level="ERROR", status_message=type(exc).__name__)
                errors.append("context version could not be updated")
            context_observation.update(
                output={"context_version_id": str(updated_context.context_version_id)}
            )

        # 7. Run analytics
        analytics_result: AnalyticsResult | None = None
        try:
            analytics_result = await analytics_agent.run(
                contract, updated_context, run_id, tracer=tracer
            )
        except Exception as exc:
            logger.warning(
                "pipeline_analytics_failed",
                extra={"error_type": type(exc).__name__},
            )
            errors.append("analytics agent failed")

        # 8. Persist evidence + insights
        if analytics_result is not None:
            if settings.recommendation_evaluator_url:
                analytics_result.insights = await _gate_recommendations(
                    request=request,
                    feature_spec=feature_spec,
                    contract=contract,
                    schema_record=schema_record,
                    context=updated_context,
                    result=analytics_result,
                    model_name=analytics_agent.model_name,
                    tracer=tracer,
                    errors=errors,
                )
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
            trace_observation=pipeline_observation,
        )
    except BaseException as exc:
        trace_error = (type(exc), exc, exc.__traceback__)
        raise
    finally:
        try:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            await spec.close()
            await events.close()
        finally:
            trace_context.__exit__(*trace_error)


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
    trace_observation: TraceObservation | None = None,
) -> PipelineRunResponse:
    duration_ms = round((time.perf_counter() - started) * 1000)
    if trace_observation is not None:
        trace_observation.update(
            output={
                "status": status_value,
                "feature_slug": feature_slug,
                "error_count": len(errors),
                "duration_ms": duration_ms,
            }
        )
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


async def _gate_recommendations(
    *,
    request: Request,
    feature_spec: str,
    contract: AnalyticsContract,
    schema_record: SchemaVersionRecord,
    context: ApprovedContext,
    result: AnalyticsResult,
    model_name: str,
    tracer: SafeLangfuseInstrumentationTracer,
    errors: list[str],
) -> list[FeatureInsight]:
    settings = request.app.state.settings
    context_version_id = str(context.context_version_id)
    context_checksum = str(context.content_sha256)
    latest_context = await run_in_threadpool(request.app.state.context_repository.latest_approved)
    current_context_id = str(latest_context.context_version_id) if latest_context else "missing"
    current_context_checksum = str(latest_context.content_sha256) if latest_context else "0" * 64
    schema_checksum = hashlib.sha256(schema_record.ddl.encode("utf-8")).hexdigest()
    evidence_by_id = {str(item.evidence_id): item for item in result.query_evidence}
    approved: list[FeatureInsight] = []
    token = settings.recommendation_evaluator_auth_token
    headers = {"authorization": f"Bearer {token.get_secret_value()}"} if token else {}
    timeout = httpx.Timeout(settings.llm_timeout_seconds)

    with tracer.observe(
        "recommendation_release_gate",
        as_type="agent",
        input={"recommendations": len(result.insights)},
        metadata={"boundary": "typescript-observability"},
    ) as gate_observation:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, insight in enumerate(result.insights):
                cited = [
                    evidence_by_id[item] for item in insight.evidence_ids if item in evidence_by_id
                ]
                sql = cited[0].sql if cited else "SELECT 0 WHERE 0"
                recommendation_id = str(uuid.uuid5(result.run_id, f"{index}:{insight.title}"))
                versions = {
                    "spec": {
                        "id": str(contract.source.spec_sha256),
                        "checksum": str(contract.source.spec_sha256),
                    },
                    "schema": {
                        "id": str(schema_record.schema_version_id),
                        "checksum": schema_checksum,
                    },
                    "businessContext": {
                        "id": context_version_id,
                        "checksum": context_checksum,
                    },
                }
                payload = {
                    "candidate": {
                        "id": recommendation_id,
                        "question": contract.feature.objective,
                        "text": f"{insight.title}. {insight.summary}",
                        "confidence": insight.confidence,
                        "featureSpec": feature_spec,
                        "businessContext": context.compact_json(),
                        "sql": sql,
                        "evidenceIds": [str(item.evidence_id) for item in cited],
                        "prompt": {
                            "name": INSIGHTS_PROMPT_NAME,
                            "version": INSIGHTS_PROMPT_VERSION,
                        },
                        "model": {
                            "provider": "openai-compatible",
                            "name": model_name,
                            "version": model_name,
                        },
                        "versions": versions,
                    },
                    "currentVersions": {
                        **versions,
                        "businessContext": {
                            "id": current_context_id,
                            "checksum": current_context_checksum,
                        },
                    },
                    "evidence": [
                        {
                            "id": str(item.evidence_id),
                            "sql": item.sql,
                            "rows": json.loads(item.result_json),
                            "checksum": hashlib.sha256(
                                item.result_json.encode("utf-8")
                            ).hexdigest(),
                            "executedAt": result.generated_at.isoformat().replace("+00:00", "Z"),
                            "latencyMs": item.latency_ms,
                        }
                        for item in cited
                    ],
                    "now": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "maxEvidenceAgeMs": settings.recommendation_max_evidence_age_ms,
                }
                try:
                    response = await client.post(
                        settings.recommendation_evaluator_url,
                        json=payload,
                        headers=headers,
                    )
                    decision = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    logger.error(
                        "recommendation_gate_unavailable",
                        extra={"error_type": type(exc).__name__},
                    )
                    errors.append("recommendation observability gate unavailable")
                    continue
                if response.status_code == 200 and decision.get("status") == "APPROVED":
                    approved.append(insight)
                else:
                    block_status = str(decision.get("status", "BLOCKED_EVALUATION"))
                    errors.append(f"recommendation blocked: {block_status}")
        gate_observation.update(
            output={"approved": len(approved), "blocked": len(result.insights) - len(approved)}
        )
    return approved


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
