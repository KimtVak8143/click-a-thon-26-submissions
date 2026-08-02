import json
import uuid
from pathlib import Path

import pytest

from app.agents.schema_planner import SchemaPlanner
from app.benchmarks.atlys import discover_feature_packages, validate_generated_schema
from app.contracts.models import AnalyticsContract
from app.profiling.profiler import SourceProfiler
from tests.test_contracts import contract_data as _contract_data

ATLYS_SPECS = Path(__file__).parents[1] / "Atlys" / "specs"


def test_discovers_every_complete_atlys_feature_package() -> None:
    if not ATLYS_SPECS.is_dir():
        pytest.skip("optional Atlys package is not present")
    specs_root = ATLYS_SPECS

    packages = discover_feature_packages(specs_root)

    assert len(packages) == 5
    assert all(package.spec_path.name == "spec.md" for package in packages)
    assert all(package.events_path.name == "events.ndjson" for package in packages)


def test_atlys_event_field_maps_to_materialized_event_name() -> None:
    artifact = Path(__file__).parents[1] / "artifacts" / "atlys_benchmark.json"
    if not artifact.is_file():
        return
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    ddl = payload["results"][0]["schema"]["ddl"]

    assert "event String" in ddl
    assert "event_name LowCardinality(String) MATERIALIZED toLowCardinality(event)" in ddl


def test_generated_schema_passes_production_invariants() -> None:
    events = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"
    profile = SourceProfiler().profile(events)
    contract = AnalyticsContract.model_validate_with_profile(
        _contract_data.__wrapped__(profile), profile
    )
    planner = SchemaPlanner(lambda: None, "clickathon1")

    schema = planner.plan(contract, uuid.uuid4(), dry_run=True)
    checks = validate_generated_schema(contract, profile.field_paths, schema.ddl)

    assert all(checks.values())
