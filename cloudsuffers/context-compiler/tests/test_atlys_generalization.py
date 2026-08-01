from pathlib import Path

import pytest

from app.contracts.intent import preferred_primary_entity_key, unsupported_pm_questions
from app.profiling.profiler import SourceProfiler

ATLYS = Path(__file__).parents[1] / "Atlys" / "specs"
pytestmark = pytest.mark.skipif(not ATLYS.is_dir(), reason="optional Atlys package is not present")


def test_group_feature_prefers_group_workflow_key() -> None:
    feature = ATLYS / "02_group_family"
    spec = (feature / "spec.md").read_text(encoding="utf-8")
    profile = SourceProfiler().profile(feature / "events.ndjson")

    assert preferred_primary_entity_key(profile, spec) == "group_id"


def test_unsupported_group_questions_are_deterministic() -> None:
    feature = ATLYS / "02_group_family"
    spec = (feature / "spec.md").read_text(encoding="utf-8")
    profile = SourceProfiler().profile(feature / "events.ndjson")

    questions = unsupported_pm_questions(spec, profile)

    assert len(questions) == 2
    assert [classification.value for _, classification in questions] == [
        "not_computable",
        "not_computable",
    ]
