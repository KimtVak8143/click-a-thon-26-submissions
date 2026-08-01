import pytest
from pydantic import ValidationError

from app.core import tracing
from app.core.config import Settings


def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_COMPILER_APP_ENV", "test")
    monkeypatch.setenv("CONTEXT_COMPILER_LOG_LEVEL", "debug")
    monkeypatch.setenv("CONTEXT_COMPILER_CLICKHOUSE_HOST", "clickhouse.internal")
    monkeypatch.setenv("CONTEXT_COMPILER_CLICKHOUSE_PORT", "8443")
    monkeypatch.setenv("CONTEXT_COMPILER_CLICKHOUSE_SECURE", "true")
    monkeypatch.setenv("CONTEXT_COMPILER_CLICKHOUSE_USERNAME", "compiler")
    monkeypatch.setenv("CONTEXT_COMPILER_CLICKHOUSE_PASSWORD", "private")
    monkeypatch.setenv("CONTEXT_COMPILER_PROFILE_EXAMPLE_LIMIT", "3")
    monkeypatch.setenv("CONTEXT_COMPILER_PROFILE_DISTINCT_LIMIT", "250")

    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.clickhouse_host == "clickhouse.internal"
    assert settings.clickhouse_port == 8443
    assert settings.clickhouse_secure is True
    assert settings.clickhouse_username == "compiler"
    assert settings.profile_example_limit == 3
    assert settings.profile_distinct_limit == 250
    assert settings.clickhouse_password is not None
    assert settings.clickhouse_password.get_secret_value() == "private"
    assert "private" not in repr(settings)


def test_empty_optional_credentials_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_COMPILER_CLICKHOUSE_USERNAME", "")
    monkeypatch.setenv("CONTEXT_COMPILER_CLICKHOUSE_PASSWORD", "")
    monkeypatch.setenv("CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("CONTEXT_COMPILER_LANGFUSE_SECRET_KEY", "")

    settings = Settings(_env_file=None)

    assert settings.clickhouse_username is None
    assert settings.clickhouse_password is None
    assert settings.langfuse_configured is False


def test_langfuse_requires_key_pair_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXT_COMPILER_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.delenv("CONTEXT_COMPILER_LANGFUSE_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="both Langfuse keys"):
        Settings(_env_file=None)


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="log_level"):
        Settings(log_level="verbose", _env_file=None)


def test_langfuse_initialization_failure_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        langfuse_enabled=True,
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        _env_file=None,
    )

    def fail_to_initialize(**_: object) -> None:
        raise ConnectionError("Langfuse is unavailable")

    monkeypatch.setattr(tracing, "Langfuse", fail_to_initialize)

    state = tracing.configure_langfuse(settings)

    assert state.status == "degraded"
    assert state.client is None
