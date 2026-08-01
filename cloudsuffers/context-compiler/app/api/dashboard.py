from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app.core.logging import get_logger
from app.profiling.models import StrictModel

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = get_logger(__name__)


class DashboardResponse(StrictModel):
    generated_at: str
    schema_timeline: list[dict] = Field(default_factory=list)
    pipeline_runs: list[dict] = Field(default_factory=list)
    context_issues: list[dict] = Field(default_factory=list)
    context_changelog: list[dict] = Field(default_factory=list)
    observability: dict = Field(default_factory=dict)
    evaluator_scores: list[dict] = Field(default_factory=list)
    recommendations: list[dict] = Field(default_factory=list)
    recent_traces: list[dict] = Field(default_factory=list)


@router.get("", response_model=DashboardResponse)
async def read_dashboard(request: Request) -> DashboardResponse:
    settings = request.app.state.settings
    metadata_database = settings.clickhouse_metadata_database
    context_repository = request.app.state.context_repository
    langfuse_state = getattr(request.app.state, "langfuse", None)
    langfuse_client = langfuse_state.client if langfuse_state is not None else None

    def query_all() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        client = context_repository._get_client()  # noqa: SLF001 - shared metadata client
        return (
            _query_schema_timeline(client, metadata_database),
            _query_pipeline_runs(client, metadata_database),
            _query_context_issues(client, metadata_database),
            _query_context_changelog(client, metadata_database),
        )

    try:
        schema_timeline, pipeline_runs, context_issues, context_changelog = await run_in_threadpool(
            query_all
        )
    except Exception as exc:
        logger.warning(
            "dashboard_query_failed",
            extra={"error_type": type(exc).__name__},
        )
        schema_timeline = []
        pipeline_runs = []
        context_issues = []
        context_changelog = []
    try:
        clickhouse_observability = await run_in_threadpool(
            _query_clickhouse_observability,
            context_repository._get_client(),  # noqa: SLF001 - shared metadata client
            metadata_database,
        )
    except Exception as exc:
        logger.warning(
            "observability_clickhouse_query_failed",
            extra={"error_type": type(exc).__name__},
        )
        clickhouse_observability = _empty_observability()
    try:
        langfuse_observability = await run_in_threadpool(
            _query_langfuse_observability,
            langfuse_client,
            settings.langfuse_base_url,
        )
    except Exception as exc:
        logger.warning(
            "observability_langfuse_query_failed",
            extra={"error_type": type(exc).__name__},
        )
        langfuse_observability = _empty_observability()
    observability = _merge_observability(clickhouse_observability, langfuse_observability)
    return DashboardResponse(
        generated_at=datetime.now(UTC).isoformat(),
        schema_timeline=schema_timeline,
        pipeline_runs=pipeline_runs,
        context_issues=context_issues,
        context_changelog=context_changelog,
        observability=observability["summary"],
        evaluator_scores=observability["scores"],
        recommendations=observability["recommendations"],
        recent_traces=observability["traces"],
    )


def _empty_observability() -> dict[str, Any]:
    return {"summary": {}, "scores": [], "recommendations": [], "traces": []}


def _query_clickhouse_observability(client: Any, metadata_database: str) -> dict[str, Any]:
    recommendations_table = f"`{metadata_database}`.`ai_recommendations`"
    evaluations_table = f"`{metadata_database}`.`ai_evaluations`"
    traces_table = f"`{metadata_database}`.`ai_traces`"
    recommendation_summary = client.query(
        "SELECT count(), countIf(status = 'APPROVED'), avgOrNull(confidence), "
        f"countIf(status != 'APPROVED') FROM {recommendations_table} "
        "WHERE created_at >= now() - INTERVAL 30 DAY",
        parameters={},
    ).result_rows
    trace_summary = client.query(
        "SELECT count(), avgOrNull(latency_ms), sum(total_cost_usd), sum(total_tokens), "
        f"countIf(status_code = 2) FROM {traces_table} FINAL "
        "WHERE started_at >= now() - INTERVAL 30 DAY",
        parameters={},
    ).result_rows
    score_rows = client.query(
        "SELECT evaluator, avg(score), avg(passed), count() "
        f"FROM {evaluations_table} WHERE created_at >= now() - INTERVAL 30 DAY "
        "GROUP BY evaluator ORDER BY evaluator",
        parameters={},
    ).result_rows
    recommendation_rows = client.query(
        "SELECT recommendation_id, trace_id, status, recommendation, confidence, "
        "model_name, prompt_version, created_at "
        f"FROM {recommendations_table} ORDER BY created_at DESC LIMIT 12",
        parameters={},
    ).result_rows
    trace_rows = client.query(
        "SELECT trace_id, name, started_at, latency_ms, total_cost_usd, total_tokens, "
        f"status_code FROM {traces_table} FINAL ORDER BY started_at DESC LIMIT 12",
        parameters={},
    ).result_rows

    recommendation_values = recommendation_summary[0] if recommendation_summary else (0, 0, None, 0)
    trace_values = trace_summary[0] if trace_summary else (0, None, 0, 0, 0)
    total_recommendations = int(recommendation_values[0] or 0)
    approved = int(recommendation_values[1] or 0)
    total_traces = int(trace_values[0] or 0)
    return {
        "summary": {
            "clickhouse_available": True,
            "trace_count": total_traces,
            "recommendation_count": total_recommendations,
            "approval_rate": approved / total_recommendations if total_recommendations else None,
            "average_confidence": _float_or_none(recommendation_values[2]),
            "failure_rate": int(recommendation_values[3] or 0) / total_recommendations
            if total_recommendations
            else None,
            "average_latency_ms": _float_or_none(trace_values[1]),
            "total_cost_usd": float(trace_values[2] or 0),
            "total_tokens": int(trace_values[3] or 0),
            "error_count": int(trace_values[4] or 0),
        },
        "scores": [
            {
                "name": str(name),
                "value": float(value or 0),
                "pass_rate": float(pass_rate or 0),
                "count": int(count or 0),
                "source": "clickhouse",
            }
            for name, value, pass_rate, count in score_rows
        ],
        "recommendations": [
            {
                "recommendation_id": str(recommendation_id),
                "trace_id": str(trace_id),
                "status": str(status),
                "recommendation": str(recommendation),
                "confidence": float(confidence),
                "model": str(model),
                "prompt_version": str(prompt_version),
                "created_at": _iso(created_at),
            }
            for (
                recommendation_id,
                trace_id,
                status,
                recommendation,
                confidence,
                model,
                prompt_version,
                created_at,
            ) in recommendation_rows
        ],
        "traces": [
            {
                "trace_id": str(trace_id),
                "name": str(name),
                "timestamp": _iso(started_at),
                "latency_ms": float(latency_ms or 0),
                "cost_usd": float(cost or 0),
                "tokens": int(tokens or 0),
                "status": "error" if int(status_code or 0) == 2 else "ok",
                "source": "clickhouse",
            }
            for trace_id, name, started_at, latency_ms, cost, tokens, status_code in trace_rows
        ],
    }


def _query_langfuse_observability(client: Any, base_url: str) -> dict[str, Any]:
    if client is None:
        return _empty_observability()
    since = datetime.now(UTC) - timedelta(days=30)
    traces_response = client.api.trace.list(
        limit=20, order_by="timestamp.desc", from_timestamp=since
    )
    scores_response = client.api.scores.get_many(limit=100, from_timestamp=since)
    observations_response = client.api.observations.get_many(
        limit=1_000,
        fields="basic,usage,metrics,model",
        from_start_time=since,
    )
    usage_by_trace: dict[str, int] = {}
    error_trace_ids: set[str] = set()
    total_observation_cost = 0.0
    for observation in observations_response.data:
        trace_id = str(observation.trace_id or "")
        usage = observation.usage_details or {}
        total_tokens = usage.get("total") or usage.get("totalTokens")
        if total_tokens is None:
            total_tokens = (usage.get("input") or usage.get("inputTokens") or 0) + (
                usage.get("output") or usage.get("outputTokens") or 0
            )
        if trace_id:
            usage_by_trace[trace_id] = usage_by_trace.get(trace_id, 0) + int(total_tokens or 0)
        total_observation_cost += float(observation.total_cost or 0)
        if str(observation.level or "").upper().endswith("ERROR") and trace_id:
            error_trace_ids.add(trace_id)
    traces = []
    for trace in traces_response.data:
        html_path = str(trace.html_path)
        traces.append(
            {
                "trace_id": str(trace.id),
                "name": str(trace.name or "unnamed-trace"),
                "timestamp": _iso(trace.timestamp),
                "latency_ms": float(trace.latency or 0) * 1_000,
                "cost_usd": float(trace.total_cost or 0),
                "tokens": usage_by_trace.get(str(trace.id), 0),
                "status": "error" if str(trace.id) in error_trace_ids else "ok",
                "source": "langfuse",
                "url": html_path
                if html_path.startswith(("http://", "https://"))
                else f"{base_url.rstrip('/')}{html_path}",
                "observations": len(trace.observations or []),
                "score_count": len(trace.scores or []),
                "environment": str(trace.environment),
            }
        )
    grouped_scores: dict[str, list[float]] = {}
    for score in scores_response.data:
        value = getattr(score, "value", None)
        if isinstance(value, int | float):
            grouped_scores.setdefault(str(score.name), []).append(float(value))
    scores = [
        {
            "name": name,
            "value": sum(values) / len(values),
            "pass_rate": None,
            "count": len(values),
            "source": "langfuse",
        }
        for name, values in sorted(grouped_scores.items())
    ]
    average_latency = sum(item["latency_ms"] for item in traces) / len(traces) if traces else None
    return {
        "summary": {
            "langfuse_available": True,
            "trace_count": len(traces),
            "average_latency_ms": average_latency,
            "total_cost_usd": sum(item["cost_usd"] for item in traces),
            "total_tokens": sum(usage_by_trace.values()),
            "error_count": len(error_trace_ids),
            "observed_cost_usd": total_observation_cost,
        },
        "scores": scores,
        "recommendations": [],
        "traces": traces,
    }


def _merge_observability(clickhouse: dict[str, Any], langfuse: dict[str, Any]) -> dict[str, Any]:
    clickhouse_summary = clickhouse["summary"]
    langfuse_summary = langfuse["summary"]
    summary = {
        **clickhouse_summary,
        "langfuse_available": bool(langfuse_summary.get("langfuse_available")),
        "clickhouse_available": bool(clickhouse_summary.get("clickhouse_available")),
    }
    if langfuse_summary:
        summary["trace_count"] = langfuse_summary.get("trace_count", summary.get("trace_count", 0))
        if not summary.get("average_latency_ms"):
            summary["average_latency_ms"] = langfuse_summary.get("average_latency_ms")
        if not summary.get("total_cost_usd"):
            summary["total_cost_usd"] = langfuse_summary.get("total_cost_usd", 0)
        if not summary.get("total_tokens"):
            summary["total_tokens"] = langfuse_summary.get("total_tokens", 0)
        if not summary.get("error_count"):
            summary["error_count"] = langfuse_summary.get("error_count", 0)
    summary["has_data"] = bool(
        summary.get("trace_count") or summary.get("recommendation_count") or langfuse["scores"]
    )
    summary["source"] = (
        "Langfuse + ClickHouse"
        if summary["langfuse_available"] and summary["clickhouse_available"]
        else "Langfuse"
        if summary["langfuse_available"]
        else "ClickHouse"
        if summary["clickhouse_available"]
        else "Unavailable"
    )
    scores_by_name = {_metric_key(item["name"]): item for item in clickhouse["scores"]}
    for score in langfuse["scores"]:
        scores_by_name[_metric_key(score["name"])] = score
    trace_by_id = {item["trace_id"]: item for item in clickhouse["traces"]}
    for trace in langfuse["traces"]:
        trace_by_id[trace["trace_id"]] = {**trace_by_id.get(trace["trace_id"], {}), **trace}
    traces = sorted(
        trace_by_id.values(), key=lambda item: item.get("timestamp") or "", reverse=True
    )
    return {
        "summary": summary,
        "scores": list(scores_by_name.values()),
        "recommendations": clickhouse["recommendations"],
        "traces": traces[:20],
    }


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _metric_key(value: Any) -> str:
    return str(value).lower().replace("-", " ").replace("_", " ")


def _query_schema_timeline(client: Any, metadata_database: str) -> list[dict]:
    table = f"`{metadata_database}`.`schema_versions`"
    query = (
        "SELECT feature_slug, version, object_inventory_json, deployed_at, created_at "
        f"FROM {table} ORDER BY created_at DESC LIMIT 10"
    )
    result = client.query(query, parameters={})
    entries = []
    for row in result.result_rows:
        feature_slug, version, inventory_json, deployed_at, created_at = row
        strategy = "dedicated_event_table"
        table_name = ""
        try:
            payload = json.loads(inventory_json)
            if isinstance(payload, dict):
                candidate_strategy = payload.get("strategy")
                if isinstance(candidate_strategy, str):
                    strategy = candidate_strategy
                tables = payload.get("tables")
                if isinstance(tables, list) and tables and isinstance(tables[0], str):
                    table_name = tables[0]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        entries.append(
            {
                "feature_slug": feature_slug,
                "version": version,
                "strategy": strategy,
                "table_name": table_name,
                "deployed_at": _iso(deployed_at),
                "created_at": _iso(created_at),
            }
        )
    return entries


def _query_pipeline_runs(client: Any, metadata_database: str) -> list[dict]:
    table = f"`{metadata_database}`.`pipeline_runs`"
    query = (
        "SELECT run_id, feature_slug, status, started_at "
        f"FROM {table} ORDER BY started_at DESC LIMIT 10"
    )
    result = client.query(query, parameters={})
    entries = []
    for row in result.result_rows:
        run_id, feature_slug, status_value, started_at = row
        entries.append(
            {
                "run_id": str(run_id),
                "feature_slug": feature_slug,
                "status": status_value,
                "started_at": _iso(started_at),
            }
        )
    return entries


def _query_context_issues(client: Any, metadata_database: str) -> list[dict]:
    table = f"`{metadata_database}`.`context_issues`"
    query = (
        "SELECT issue_code, category, severity, title FROM "
        f"{table} WHERE status = 'open' ORDER BY updated_at DESC LIMIT 100"
    )
    result = client.query(query, parameters={})
    entries = []
    for row in result.result_rows:
        issue_code, category, severity, title = row
        entries.append(
            {
                "issue_code": issue_code,
                "category": category,
                "severity": severity,
                "title": title,
            }
        )
    return entries


def _query_context_changelog(client: Any, metadata_database: str) -> list[dict]:
    table = f"`{metadata_database}`.`context_changelog`"
    query = (
        f"SELECT change_type, summary, created_at FROM {table} ORDER BY created_at DESC LIMIT 20"
    )
    result = client.query(query, parameters={})
    entries = []
    for row in result.result_rows:
        change_type, summary, created_at = row
        entries.append(
            {
                "change_type": change_type,
                "summary": summary,
                "created_at": _iso(created_at),
            }
        )
    return entries


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
