from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.instrumentation import InstrumentationAgent
from app.agents.schema_planner import SchemaPlanner
from app.clickhouse.client import build_clickhouse_client
from app.context.repository import ClickHouseContextRepository
from app.core.config import Settings
from app.core.tracing import (
    SafeLangfuseInstrumentationTracer,
    configure_langfuse,
    shutdown_langfuse,
)
from app.llm.provider import OpenAICompatibleProvider
from app.metrics.baseline import BaselineMetricsService
from app.profiling.profiler import ProfilerOptions, SourceProfiler


@dataclass(frozen=True)
class FeaturePackage:
    name: str
    spec_path: Path
    events_path: Path


def discover_feature_packages(specs_root: Path) -> list[FeaturePackage]:
    """Discover every complete spec/events package, including an unseen sixth package."""

    packages = []
    for directory in sorted(path for path in specs_root.iterdir() if path.is_dir()):
        spec_path = directory / "spec.md"
        events_path = directory / "events.ndjson"
        if spec_path.is_file() and events_path.is_file():
            packages.append(FeaturePackage(directory.name, spec_path, events_path))
    if not packages:
        raise RuntimeError(f"no spec.md/events.ndjson packages found under {specs_root}")
    return packages


async def run_atlys_benchmark(settings: Settings, specs_root: Path) -> dict[str, Any]:
    packages = discover_feature_packages(specs_root)

    def client_factory() -> Any:
        return build_clickhouse_client(settings)

    context_repository = ClickHouseContextRepository(
        client_factory, settings.clickhouse_metadata_database
    )
    baseline_service = BaselineMetricsService(
        client_factory,
        settings.clickhouse_database,
        settings.clickhouse_metadata_database,
    )
    provider = OpenAICompatibleProvider(settings)
    profiler = SourceProfiler(
        ProfilerOptions(
            example_limit=settings.profile_example_limit,
            distinct_limit=settings.profile_distinct_limit,
            example_string_length=settings.profile_example_string_length,
        )
    )
    agent = InstrumentationAgent(
        provider,
        context_max_chars=settings.contract_context_max_chars,
        total_timeout_seconds=settings.llm_total_generation_timeout_seconds,
    )
    planner = SchemaPlanner(client_factory, settings.clickhouse_database)
    langfuse_state = configure_langfuse(settings)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    try:
        approved_context = context_repository.latest_approved()
        if approved_context is None:
            raise RuntimeError("an approved context version is required before benchmarking")
        baseline = baseline_service.precompute()
        context_payload = json.loads(approved_context.compact_json())
        evidence_ids = list(approved_context.evidence_ids)
        context_payload["baseline_metrics"] = baseline.compact()
        evidence_ids.extend(baseline.evidence_ids)
        context_json = json.dumps(context_payload, sort_keys=True, default=str)

        for package in packages:
            feature_started = time.perf_counter()
            run_id = uuid.uuid4()
            spec = package.spec_path.read_text(encoding="utf-8")
            profile = profiler.profile(package.events_path)
            tracer = SafeLangfuseInstrumentationTracer(
                langfuse_state.client,
                run_id.hex,
                feature_name=package.name,
                tags=["atlys-benchmark", "instrumentation"],
            )
            with tracer.observe(
                "atlys-feature-benchmark",
                as_type="chain",
                input={
                    "package": package.name,
                    "rows": profile.file.valid_row_count,
                    "event_names": sorted(profile.event_names),
                },
                metadata={
                    "run_id": str(run_id),
                    "context_version_id": str(approved_context.context_version_id),
                    "baseline_snapshot_id": str(baseline.snapshot_id),
                },
            ) as observation:
                generation = await agent.generate_contract(
                    spec,
                    profile,
                    context_summary=context_json,
                    context_version_id=approved_context.context_version_id,
                    context_content_sha256=approved_context.content_sha256,
                    context_evidence_ids=sorted(set(evidence_ids)),
                    tracer=tracer,
                    run_id=run_id,
                )
                item: dict[str, Any] = {
                    "package": package.name,
                    "run_id": str(run_id),
                    "trace_id": generation.trace_id,
                    "rows": profile.file.valid_row_count,
                    "observed_events": sorted(profile.event_names),
                    "status": generation.validation_status,
                    "attempts": generation.attempts,
                    "warnings": generation.warnings,
                    "errors": [error.model_dump(mode="json") for error in generation.errors],
                }
                if generation.analytics_contract is not None:
                    contract = generation.analytics_contract
                    with tracer.observe(
                        "plan-schema",
                        as_type="span",
                        input={"feature_slug": contract.feature.slug, "dry_run": True},
                    ) as schema_observation:
                        schema = planner.plan(contract, run_id, dry_run=True)
                        checks = validate_generated_schema(
                            contract, profile.field_paths, schema.ddl
                        )
                        schema_observation.update(
                            output={
                                "table_name": schema.table_name,
                                "strategy": schema.strategy_name,
                                "ddl_sha256": hashlib.sha256(schema.ddl.encode()).hexdigest(),
                                "checks": checks,
                            }
                        )
                    item.update(
                        {
                            "feature_slug": contract.feature.slug,
                            "primary_entity": contract.primary_entity,
                            "entity_count": len(contract.entities),
                            "metric_count": len(contract.metrics),
                            "open_question_count": len(contract.open_questions),
                            "contract": contract.model_dump(mode="json"),
                            "schema": {
                                "strategy": schema.strategy_name,
                                "table_name": schema.table_name,
                                "ddl": schema.ddl,
                                "checks": checks,
                            },
                        }
                    )
                    if not all(checks.values()):
                        item["status"] = "schema_invalid"
                item["duration_ms"] = round((time.perf_counter() - feature_started) * 1000)
                observation.update(
                    output={
                        "status": item["status"],
                        "feature_slug": item.get("feature_slug"),
                        "schema_checks": item.get("schema", {}).get("checks"),
                    }
                )
                results.append(item)
    finally:
        context_repository.close()
        baseline_service.close()
        planner.close()
        await provider.aclose()
        shutdown_langfuse(langfuse_state)

    return {
        "benchmark_version": "atlys_dynamic_spec_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "specs_root": str(specs_root),
        "discovered_package_count": len(packages),
        "baseline_snapshot_id": str(baseline.snapshot_id),
        "context_version_id": str(approved_context.context_version_id),
        "all_valid": all(item["status"] == "valid" for item in results),
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "results": results,
    }


def validate_generated_schema(
    contract: Any, observed_fields: frozenset[str], ddl: str
) -> dict[str, bool]:
    primary = next(
        (entity.field_path for entity in contract.entities if entity.role.value == "primary"),
        None,
    )
    mapped_fields = {
        _safe_identifier(field.source_path)
        for field in contract.fields
        if not field.spec_only and field.source_path in observed_fields
    }
    missing_fields = {field for field in mapped_fields if not _ddl_declares_column(ddl, field)}
    return {
        "all_observed_contract_fields_mapped": not missing_fields,
        "monthly_partition": "PARTITION BY toYYYYMM(timestamp)" in ddl,
        "retention_ttl": bool(re.search(r"TTL timestamp \+ INTERVAL \d+ DAY DELETE", ddl)),
        "primary_workflow_ordering_key": (
            primary is None
            or f"ORDER BY ({_safe_identifier(primary)}, event_name, timestamp)" in ddl
        ),
        "aggregate_state_is_valid": "countState(DISTINCT" not in ddl,
        "funnel_view_present_when_required": not contract.funnels
        or "CREATE MATERIALIZED VIEW" in ddl,
        "ddl_has_no_line_comment_columns": " -- scoped to events:" not in ddl,
    }


def _safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "field"
    return f"_{cleaned}" if cleaned[0].isdigit() else cleaned


def _ddl_declares_column(ddl: str, column: str) -> bool:
    return re.search(rf"^\s*{re.escape(column)}\s+", ddl, re.MULTILINE) is not None
