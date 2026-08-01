import asyncio
import hashlib
import io
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette.requests import Request

import app.api.contracts as contracts_api
from app.context.bootstrap import build_base_context_bundle
from app.context.repository import InMemoryContextRepository
from app.core.config import Settings
from app.llm.fake import FakeStructuredGenerationProvider
from app.llm.provider import ProviderError, ProviderFailureCategory
from app.main import create_app
from app.profiling.profiler import SourceProfiler
from tests.test_instrumentation_agent import SPEC, BlockingProvider, contract_data, encoded

EVENTS = Path(__file__).parent / "fixtures" / "express_checkout_events.ndjson"
BASE_CONTEXT = Path(__file__).parents[1] / "docs" / "base_context.md"


def approved_context_repository() -> InMemoryContextRepository:
    repository = InMemoryContextRepository()
    repository.persist_bootstrap(build_base_context_bundle(BASE_CONTEXT))
    return repository


def build_client(provider: FakeStructuredGenerationProvider) -> TestClient:
    settings = Settings(langfuse_enabled=False, _env_file=None)
    return TestClient(
        create_app(
            settings=settings,
            structured_provider=provider,
            context_repository=approved_context_repository(),
        )
    )


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
    assert body["context_version_id"]
    assert len(body["context_content_sha256"]) == 64
    assert body["analytics_contract"]["context_version_id"] == body["context_version_id"]
    assert list(tmp_path.iterdir()) == []


def test_contract_generate_accepts_single_contract_intent_provider_envelope() -> None:
    profile = SourceProfiler().profile(EVENTS)
    provider = FakeStructuredGenerationProvider(
        [encoded({"ContractIntent": contract_data(profile)})]
    )

    with build_client(provider) as client:
        response = client.post(
            "/contracts/generate",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": (
                    "events.ndjson",
                    EVENTS.read_bytes(),
                    "application/x-ndjson",
                ),
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["validation_status"] == "valid"
    assert body["attempts"] == 1
    assert len(provider.requests) == 1
    assert any("ContractIntent was unwrapped" in item for item in body["warnings"])


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


def test_contract_generate_returns_structured_provider_timeout() -> None:
    provider = FakeStructuredGenerationProvider(
        [
            ProviderError(
                ProviderFailureCategory.PROVIDER_TIMEOUT,
                "LLM provider request timed out",
            )
        ]
    )

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
    assert body["errors"][0]["code"] == "provider_timeout"
    assert body["attempts"] == 1
    assert len(provider.requests) == 1


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
    provider = FakeStructuredGenerationProvider([encoded(candidate)])

    with build_client(provider) as client:
        response = client.post(
            "/contracts/generate",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
        )

    body = response.json()
    assert body["validation_status"] == "valid"
    assert (
        body["analytics_contract"]["source"]["spec_sha256"]
        == hashlib.sha256(SPEC.encode()).hexdigest()
    )


def test_temporary_file_cleanup_after_task_cancellation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_named_temporary_file = tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        return original_named_temporary_file(dir=tmp_path, **kwargs)

    monkeypatch.setattr(contracts_api, "NamedTemporaryFile", temporary_file)
    provider = BlockingProvider()
    settings = Settings(
        langfuse_enabled=False,
        llm_total_generation_timeout_seconds=60,
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        structured_provider=provider,
        context_repository=approved_context_repository(),
    )
    spec_file = io.BytesIO(SPEC.encode())
    events_file = io.BytesIO(EVENTS.read_bytes())
    spec_upload = UploadFile(file=spec_file, filename="feature.md", size=len(spec_file.getvalue()))
    events_upload = UploadFile(
        file=events_file,
        filename="events.ndjson",
        size=len(events_file.getvalue()),
    )
    request = Request({"type": "http", "app": app})

    async def scenario() -> None:
        provider.started = asyncio.Event()
        task = asyncio.create_task(
            contracts_api.generate_contract(
                request,
                spec_upload,
                events_upload,
                app.state.source_profiler,
                app.state.instrumentation_agent,
                app.state.context_repository,
            )
        )
        await provider.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert provider.cancelled is True
    assert list(tmp_path.iterdir()) == []
    assert spec_file.closed is True
    assert events_file.closed is True


def test_contract_generation_blocks_before_provider_without_approved_context() -> None:
    provider = FakeStructuredGenerationProvider([])
    settings = Settings(langfuse_enabled=False, _env_file=None)
    app = create_app(
        settings=settings,
        structured_provider=provider,
        context_repository=InMemoryContextRepository(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/contracts/generate",
            files={
                "spec": ("feature.md", SPEC.encode(), "text/markdown"),
                "events": ("events.ndjson", EVENTS.read_bytes(), "application/x-ndjson"),
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["validation_status"] == "blocked"
    assert body["errors"][0]["code"] == "missing_approved_context"
    assert body["attempts"] == 0
    assert provider.requests == []
