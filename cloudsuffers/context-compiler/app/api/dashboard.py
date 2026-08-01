from __future__ import annotations

import json
from datetime import UTC, datetime
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


@router.get("", response_model=DashboardResponse)
async def read_dashboard(request: Request) -> DashboardResponse:
    settings = request.app.state.settings
    metadata_database = settings.clickhouse_metadata_database
    context_repository = request.app.state.context_repository

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
    return DashboardResponse(
        generated_at=datetime.now(UTC).isoformat(),
        schema_timeline=schema_timeline,
        pipeline_runs=pipeline_runs,
        context_issues=context_issues,
        context_changelog=context_changelog,
    )


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
