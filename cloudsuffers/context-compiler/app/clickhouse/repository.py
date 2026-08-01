from collections.abc import Callable
from typing import Any, Protocol


class ClickHouseHealthRepositoryProtocol(Protocol):
    def ping(self) -> bool: ...


class ClickHouseMigrationRepositoryProtocol(Protocol):
    def execute(self, statement: str) -> None: ...


class ClickHouseHealthRepository:
    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def ping(self) -> bool:
        return bool(self._get_client().ping())

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class ClickHouseMigrationRepository:
    def __init__(self, client: Any) -> None:
        self._client = client

    def execute(self, statement: str) -> None:
        self._client.command(statement)

    def close(self) -> None:
        self._client.close()
