from collections.abc import Sequence

from app.llm.provider import (
    ProviderHealthResult,
    StructuredGenerationRequest,
    StructuredGenerationResponse,
    TokenUsage,
)


class FakeStructuredGenerationProvider:
    """Deterministic queued provider for agent and API tests."""

    def __init__(
        self,
        responses: Sequence[str | Exception | StructuredGenerationResponse],
        *,
        model_name: str = "fake-model",
        health_result: ProviderHealthResult | None = None,
    ) -> None:
        self._responses = list(responses)
        self._model_name = model_name
        self.requests: list[StructuredGenerationRequest] = []
        self.closed = False
        self.health_result = health_result or ProviderHealthResult(
            status="ok",
            configured=True,
            reachable=True,
            model=model_name,
            model_available=True,
            latency_ms=1,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    async def generate(self, request: StructuredGenerationRequest) -> StructuredGenerationResponse:
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("fake provider response queue exhausted")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, StructuredGenerationResponse):
            return response
        return StructuredGenerationResponse(
            content=response,
            model=self.model_name,
            latency_ms=1,
            usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        )

    async def aclose(self) -> None:
        self.closed = True

    async def health(self) -> ProviderHealthResult:
        return self.health_result
