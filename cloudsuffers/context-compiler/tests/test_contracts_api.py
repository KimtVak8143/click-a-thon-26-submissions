import hashlib
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.contracts as contracts_api
from app.core.config import Settings
from app.llm.fake import FakeStructuredGenerationProvider
from app.main import create_app
from app.profiling.profiler import SourceProfiler
from tests.test_instrumentation_agent import SPEC, contract_data, encoded

EVENTS = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"


def build_client(provider: FakeStructuredGenerationProvider) -> TestClient:
    settings = Settings(langfuse_enabled=False, _env_file=None)
    return TestClient(create_app(settings=settings, structured_provider=provider))


def test_contract_generate_success_and_temporary_file_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile = SourceProfiler().profile(EVENTS)
    provider = FakeStructuredGenerationProvider([encoded(contract_data(profile))])
    original_named_temporary_file = tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        return original_named_temporary_file(dir=tmp_path, **kwargs)

    monkeypatch.setattr(contracts_api, "NamedTemporaryFile", temporary_file)
    with build_client(provider) as client:
        response = client.post(
            "/contracts/generate",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "valid"
    assert body["analytics_contract"]["contract_version"] == "1.0"
    assert body["source_profile"]["file"]["sha256"] == profile.file.sha256
    assert len(body["trace_id"]) == 32
    assert body["run_id"]
    assert list(tmp_path.iterdir()) == []


def test_contract_generate_returns_structured_blocked_result() -> None:
    provider = FakeStructuredGenerationProvider(["bad", "bad", "bad"])

    with build_client(provider) as client:
        response = client.post(
            "/contracts/generate",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "blocked"
    assert body["analytics_contract"] is None
    assert body["attempts"] == 3
    assert body["errors"][0]["code"] == "invalid_json"


def test_contract_generate_upload_failure_is_structured_and_cleans_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider = FakeStructuredGenerationProvider([])
    original_named_temporary_file = tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        return original_named_temporary_file(dir=tmp_path, **kwargs)

    monkeypatch.setattr(contracts_api, "NamedTemporaryFile", temporary_file)
    with build_client(provider) as client:
        response = client.post(
            "/contracts/generate",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", b"not-json\n", "application/x-ndjson"),
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_valid_rows"
    assert list(tmp_path.iterdir()) == []
    assert provider.requests == []


def test_contract_generate_uses_exact_spec_checksum() -> None:
    profile = SourceProfiler().profile(EVENTS)
    candidate = contract_data(profile)
    assert candidate["source"]["spec_sha256"] == hashlib.sha256(SPEC.encode()).hexdigest()
    provider = FakeStructuredGenerationProvider([json.dumps(candidate)])

    with build_client(provider) as client:
        response = client.post(
            "/contracts/generate",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
        )

    assert response.json()["validation_status"] == "valid"
