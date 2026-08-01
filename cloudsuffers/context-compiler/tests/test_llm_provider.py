import asyncio
import json

import httpx
import pytest

from app.core.config import Settings
from app.llm.provider import (
    OpenAICompatibleProvider,
    ProviderError,
    ProviderFailureCategory,
    ProviderMessage,
    StructuredGenerationRequest,
)


def test_unprefixed_llm_environment_configuration(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "configured-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")

    settings = Settings(_env_file=None)

    assert settings.llm_configured is True
    assert settings.llm_base_url == "https://llm.example/v1"
    assert settings.llm_api_key.get_secret_value() == "test-key"
    assert settings.llm_model == "configured-model"
    assert settings.llm_timeout_seconds == 12.5
    assert settings.llm_max_retries == 1


def test_openai_compatible_provider_is_lazy_and_requests_strict_json_schema() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "returned-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
            },
        )

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    settings = Settings(
        llm_base_url="https://llm.example/v1",
        llm_api_key="secret-key",
        llm_model="configured-model",
        llm_structured_output_mode="json_schema",
        _env_file=None,
    )
    provider = OpenAICompatibleProvider(settings, client_factory=client_factory)
    request = StructuredGenerationRequest(
        messages=[ProviderMessage(role="user", content="data")],
        json_schema={"type": "object", "additionalProperties": False},
        schema_name="result",
    )

    assert provider.initialized is False
    response = asyncio.run(provider.generate(request))

    assert provider.initialized is True
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-key"
    assert captured["body"]["model"] == "configured-model"
    assert captured["body"]["stream"] is False
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["max_tokens"] == 2500
    assert captured["body"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "result",
            "strict": True,
            "schema": {"type": "object", "additionalProperties": False},
        },
    }
    assert response.model == "returned-model"
    assert response.usage.total_tokens == 8

    asyncio.run(provider.aclose())
    assert provider.initialized is False


def test_json_object_payload_uses_configured_generation_controls() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    settings = Settings(
        llm_base_url="https://llm.example/v1",
        llm_api_key="key",
        llm_model="model",
        llm_structured_output_mode="json_object",
        llm_temperature=0.25,
        llm_max_output_tokens=321,
        _env_file=None,
    )
    provider = OpenAICompatibleProvider(
        settings,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    asyncio.run(provider.generate(_request()))

    assert captured["stream"] is False
    assert captured["temperature"] == 0.25
    assert captured["max_tokens"] == 321
    assert captured["response_format"] == {"type": "json_object"}
    asyncio.run(provider.aclose())


def test_http_client_is_reused_and_closed() -> None:
    clients = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    def client_factory(**kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    provider = OpenAICompatibleProvider(_settings(), client_factory=client_factory)

    async def exercise() -> None:
        await provider.generate(_request())
        await provider.generate(_request())
        assert len(clients) == 1
        assert clients[0].is_closed is False
        await provider.aclose()

    asyncio.run(exercise())

    assert clients[0].is_closed is True
    assert provider.initialized is False


def test_provider_timeout_is_categorized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret timeout detail", request=request)

    provider = OpenAICompatibleProvider(
        _settings(llm_max_retries=0),
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    with pytest.raises(ProviderError) as captured:
        asyncio.run(provider.generate(_request()))

    assert captured.value.category == ProviderFailureCategory.PROVIDER_TIMEOUT
    assert "secret timeout detail" not in str(captured.value)
    asyncio.run(provider.aclose())


def test_invalid_provider_envelope_is_categorized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OpenAICompatibleProvider(
        _settings(),
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    with pytest.raises(ProviderError) as captured:
        asyncio.run(provider.generate(_request()))

    assert captured.value.category == ProviderFailureCategory.INVALID_PROVIDER_RESPONSE
    asyncio.run(provider.aclose())


def test_http_error_is_bounded_sanitized_and_structured() -> None:
    secret = "top-secret-key"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "invalid_request",
                    "message": f"Authorization: Bearer {secret} " + ("x" * 500),
                }
            },
        )

    provider = OpenAICompatibleProvider(
        _settings(llm_api_key=secret, llm_max_retries=0),
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    with pytest.raises(ProviderError) as captured:
        asyncio.run(provider.generate(_request()))

    error = captured.value
    assert error.category == ProviderFailureCategory.PROVIDER_HTTP_ERROR
    assert error.status_code == 400
    assert error.error_code == "invalid_request"
    assert secret not in error.safe_message
    assert len(error.safe_message) <= 160
    asyncio.run(provider.aclose())


def test_provider_health_checks_models_without_generation() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "model"}]})

    provider = OpenAICompatibleProvider(
        _settings(),
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = asyncio.run(provider.health())

    assert result.status == "ok"
    assert result.reachable is True
    assert result.model_available is True
    assert paths == ["/v1/models"]
    asyncio.run(provider.aclose())


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        messages=[ProviderMessage(role="user", content="return JSON")],
        json_schema={"type": "object", "additionalProperties": False},
        schema_name="result",
    )


def _settings(**overrides) -> Settings:
    values = {
        "llm_base_url": "https://llm.example/v1",
        "llm_api_key": "key",
        "llm_model": "model",
        "llm_max_retries": 0,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)
