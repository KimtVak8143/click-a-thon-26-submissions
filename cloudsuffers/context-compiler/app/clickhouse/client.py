from typing import Any

import clickhouse_connect

from app.core.config import Settings


def build_clickhouse_client(settings: Settings) -> Any:
    kwargs: dict[str, Any] = {
        "host": settings.clickhouse_host,
        "port": settings.clickhouse_port,
        "secure": settings.clickhouse_secure,
        "database": settings.clickhouse_database,
        "connect_timeout": settings.clickhouse_connect_timeout_seconds,
        "send_receive_timeout": settings.clickhouse_query_timeout_seconds,
    }
    if settings.clickhouse_username:
        kwargs["username"] = settings.clickhouse_username
    if settings.clickhouse_password:
        kwargs["password"] = settings.clickhouse_password.get_secret_value()
    return clickhouse_connect.get_client(**kwargs)
