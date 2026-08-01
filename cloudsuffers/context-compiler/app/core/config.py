from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CONTEXT_COMPILER_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Context Compiler"
    app_env: str = "development"
    log_level: str = "INFO"

    profile_max_upload_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    profile_example_limit: int = Field(default=5, ge=0, le=100)
    profile_distinct_limit: int = Field(default=10_000, ge=1, le=1_000_000)
    profile_example_string_length: int = Field(default=128, ge=1, le=10_000)
    profile_upload_chunk_bytes: int = Field(default=1024 * 1024, ge=1024, le=16 * 1024 * 1024)

    contract_spec_max_upload_bytes: int = Field(default=1024 * 1024, ge=1)
    contract_context_max_chars: int = Field(default=8_000, ge=0, le=100_000)

    llm_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_BASE_URL", "CONTEXT_COMPILER_LLM_BASE_URL"),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "CONTEXT_COMPILER_LLM_API_KEY"),
    )
    llm_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_MODEL", "CONTEXT_COMPILER_LLM_MODEL"),
    )
    llm_timeout_seconds: float = Field(
        default=30,
        gt=0,
        le=300,
        validation_alias=AliasChoices(
            "LLM_TIMEOUT_SECONDS", "CONTEXT_COMPILER_LLM_TIMEOUT_SECONDS"
        ),
    )
    llm_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        validation_alias=AliasChoices("LLM_MAX_RETRIES", "CONTEXT_COMPILER_LLM_MAX_RETRIES"),
    )

    clickhouse_host: str = "localhost"
    clickhouse_port: int = Field(default=8123, ge=1, le=65535)
    clickhouse_secure: bool = False
    clickhouse_username: str | None = None
    clickhouse_password: SecretStr | None = None
    clickhouse_database: str = "default"
    clickhouse_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    clickhouse_query_timeout_seconds: int = Field(default=10, ge=1, le=300)

    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

    @field_validator(
        "clickhouse_username",
        "langfuse_public_key",
        "llm_base_url",
        "llm_model",
        mode="before",
    )
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("clickhouse_password", "langfuse_secret_key", "llm_api_key", mode="before")
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalized

    @model_validator(mode="after")
    def validate_langfuse_credentials(self) -> "Settings":
        has_public_key = self.langfuse_public_key is not None
        has_secret_key = self.langfuse_secret_key is not None
        if self.langfuse_enabled and has_public_key != has_secret_key:
            raise ValueError("both Langfuse keys are required when Langfuse is enabled")
        return self

    @property
    def langfuse_configured(self) -> bool:
        return bool(self.langfuse_enabled and self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
