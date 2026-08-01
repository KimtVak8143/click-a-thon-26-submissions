import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.profiles as profiles_api
from app.core.config import Settings
from app.main import create_app


def build_client(**settings_overrides: object) -> TestClient:
    settings = Settings(langfuse_enabled=False, _env_file=None, **settings_overrides)
    return TestClient(create_app(settings=settings))


def test_profile_upload_success_and_temporary_file_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_named_temporary_file = tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        return original_named_temporary_file(dir=tmp_path, **kwargs)

    monkeypatch.setattr(profiles_api, "NamedTemporaryFile", temporary_file)
    payload = (
        b'{"event_id":"event-1","event_name":"started",'
        b'"event_time":"2026-01-01T00:00:00Z","application_id":"app-1"}\n'
    )

    with build_client() as client:
        response = client.post(
            "/profiles",
            files={"events": ("events.ndjson", payload, "application/x-ndjson")},
        )

    assert response.status_code == 200
    assert response.json()["file"]["valid_row_count"] == 1
    assert response.json()["candidate_identifiers"][0]["field_path"] == "application_id"
    assert list(tmp_path.iterdir()) == []


def test_profile_upload_rejects_malformed_ndjson_and_cleans_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_named_temporary_file = tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        return original_named_temporary_file(dir=tmp_path, **kwargs)

    monkeypatch.setattr(profiles_api, "NamedTemporaryFile", temporary_file)
    payload = b'{"event_name":"started","event_time":"2026-01-01T00:00:00Z"}\nnot-json\n'

    with build_client() as client:
        response = client.post(
            "/profiles",
            files={"events": ("events.ndjson", payload, "application/x-ndjson")},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "malformed_ndjson",
            "message": "events file contains 1 malformed row(s)",
        }
    }
    assert list(tmp_path.iterdir()) == []


def test_profile_upload_rejects_invalid_filename() -> None:
    with build_client() as client:
        response = client.post(
            "/profiles",
            files={"events": ("events.json", b"{}\n", "application/json")},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_filename"


def test_profile_upload_rejects_configured_maximum_size() -> None:
    with build_client(profile_max_upload_bytes=10) as client:
        response = client.post(
            "/profiles",
            files={
                "events": ("events.ndjson", b'{"event_name":"large"}\n', "application/x-ndjson")
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "file_too_large"


def test_profile_upload_rejects_file_without_valid_rows() -> None:
    with build_client() as client:
        response = client.post(
            "/profiles",
            files={"events": ("events.ndjson", b"\n\n", "application/x-ndjson")},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_valid_rows"


def test_profile_cli_writes_stable_json(tmp_path: Path) -> None:
    events = Path(__file__).parent / "fixtures" / "status_sharing_events.ndjson"
    output = tmp_path / "profile.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "profile",
            "--events",
            str(events),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"profile_version": "1.0"' in output.read_text(encoding="utf-8")
