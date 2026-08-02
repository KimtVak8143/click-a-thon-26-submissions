from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from app.agents.analytics import ProbeResult
from app.core.logging import get_logger
from app.core.tracing import SafeLangfuseInstrumentationTracer
from app.profiling.models import StrictModel

router = APIRouter(prefix="/analytics", tags=["analytics"])
logger = get_logger(__name__)

_ALLOWED_MODES = {"data", "context_audit"}


class ProbeRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2_000)
    mode: str = Field(default="data")


class ProbeFindingResponse(StrictModel):
    title: str
    summary: str
    confidence: float
    category: str
    evidence_ids: list[str] = Field(default_factory=list)


class ProbeResponse(StrictModel):
    run_id: str
    question: str
    mode: str
    answer: str
    findings: list[ProbeFindingResponse] = Field(default_factory=list)
    tables_examined: list[str] = Field(default_factory=list)


class ProbeErrorDetail(StrictModel):
    code: str
    message: str


class ProbeErrorResponse(StrictModel):
    detail: ProbeErrorDetail


@router.post(
    "/probe",
    response_model=ProbeResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProbeErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ProbeErrorResponse},
    },
)
async def run_probe(request: Request, body: ProbeRequest) -> ProbeResponse:
    """Answer an open-ended analytics question against the Analytics Agent.

    `mode="data"` (default) gathers evidence across every event-shaped table the
    context layer currently knows about. `mode="context_audit"` skips ClickHouse and
    asks the model to critique the approved context's own declared content — use this
    for questions like "is anything in the base context wrong, stale, or
    self-contradictory?".
    """
    if body.mode not in _ALLOWED_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_mode",
                "message": f"mode must be one of {sorted(_ALLOWED_MODES)}",
            },
        )

    context_repository = request.app.state.context_repository
    analytics_agent = request.app.state.analytics_agent
    langfuse_state = getattr(request.app.state, "langfuse", None)
    langfuse_client = langfuse_state.client if langfuse_state is not None else None

    context = context_repository.latest_approved()
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "no_approved_context",
                "message": "no approved context version is available",
            },
        )

    run_id = uuid.uuid4()
    tracer = SafeLangfuseInstrumentationTracer(
        langfuse_client,
        run_id.hex,
        feature_name="analytics_probe",
        tags=["api", "analytics-agent", "probe"],
    )
    try:
        result: ProbeResult = await analytics_agent.run_probe(
            body.question,
            context,
            run_id,
            mode=body.mode,
            tracer=tracer,
        )
    except Exception as exc:
        logger.warning("analytics_probe_endpoint_failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "probe_failed",
                "message": "the analytics probe could not complete",
            },
        ) from exc

    return ProbeResponse(
        run_id=str(run_id),
        question=result.question,
        mode=result.mode,
        answer=result.answer,
        findings=[
            ProbeFindingResponse(
                title=finding.title,
                summary=finding.summary,
                confidence=finding.confidence,
                category=finding.category,
                evidence_ids=finding.evidence_ids,
            )
            for finding in result.findings
        ],
        tables_examined=result.tables_examined,
    )
