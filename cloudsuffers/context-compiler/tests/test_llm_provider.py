import asyncio
import json

import httpx

from app.core.config import Settings
from app.llm.provider import (
    OpenAICompatibleProvider,
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
