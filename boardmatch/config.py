"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator


class AppEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseModel):
    """Typed BoardMatch settings that can be constructed directly in tests."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    app_env: AppEnvironment = AppEnvironment.LOCAL
    database_url: str = "sqlite:///./boardmatch.db"
    auth_issuer: str | None = None
    auth_audience: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_deployment: str | None = None
    azure_storage_account: str | None = None
    storage_encryption_required: bool = True
    document_retention_days: int = 365
    extracted_text_retention_days: int = 90
    audit_log_retention_days: int = 90
    network_data_retention_days: int = 365
    log_level: str = "INFO"

    @field_validator(
        "auth_issuer",
        "auth_audience",
        "azure_openai_endpoint",
        "azure_openai_api_key",
        "azure_openai_deployment",
        "azure_storage_account",
        mode="before",
    )
    @classmethod
    def convert_empty_optional_values(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("DATABASE_URL must be a valid URL")
        return value

    @field_validator("auth_issuer", "azure_openai_endpoint")
    @classmethod
    def validate_optional_url(cls, value: str | None) -> str | None:
        if value is not None and urlparse(value).scheme not in {"http", "https"}:
            raise ValueError("must be an HTTP(S) URL")
        return value

    @field_validator("azure_storage_account")
    @classmethod
    def validate_storage_account(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.isalnum() or not value.islower() or not 3 <= len(value) <= 24
        ):
            raise ValueError("must be a valid Azure Storage account name")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard Python log level")
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.app_env != AppEnvironment.PRODUCTION:
            return self

        required = (
            ("DATABASE_URL", self.database_url),
            ("AUTH_ISSUER", self.auth_issuer),
            ("AUTH_AUDIENCE", self.auth_audience),
            ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
            ("AZURE_OPENAI_API_KEY", self.azure_openai_api_key),
            ("AZURE_OPENAI_DEPLOYMENT", self.azure_openai_deployment),
            ("AZURE_STORAGE_ACCOUNT", self.azure_storage_account),
        )
        missing = [name for name, value in required if not value]
        if missing:
            raise ValueError(
                f"Missing required production settings: {', '.join(missing)}"
            )
        if self.database_url.startswith("sqlite:"):
            raise ValueError("DATABASE_URL must not use SQLite in production")
        if self.auth_issuer is not None and not self.auth_issuer.startswith("https://"):
            raise ValueError("AUTH_ISSUER must use HTTPS in production")
        if (
            self.azure_openai_endpoint is not None
            and not self.azure_openai_endpoint.startswith("https://")
        ):
            raise ValueError("AZURE_OPENAI_ENDPOINT must use HTTPS in production")
        if self.log_level == "DEBUG":
            raise ValueError("LOG_LEVEL must not be DEBUG in production")
        if self.storage_encryption_required and not self.azure_storage_account:
            raise ValueError(
                "AZURE_STORAGE_ACCOUNT is required when storage encryption is enforced in production"
            )
        return self

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "Settings":
        """Load settings without requiring process-wide environment mutation in tests."""
        source = os.environ if environ is None else environ
        values = {
            field: source[name]
            for name, field in _ENVIRONMENT_FIELDS.items()
            if name in source
        }
        return cls(**values)


_ENVIRONMENT_FIELDS = {
    "APP_ENV": "app_env",
    "DATABASE_URL": "database_url",
    "AUTH_ISSUER": "auth_issuer",
    "AUTH_AUDIENCE": "auth_audience",
    "AZURE_OPENAI_ENDPOINT": "azure_openai_endpoint",
    "AZURE_OPENAI_API_KEY": "azure_openai_api_key",
    "AZURE_OPENAI_DEPLOYMENT": "azure_openai_deployment",
    "AZURE_STORAGE_ACCOUNT": "azure_storage_account",
    "STORAGE_ENCRYPTION_REQUIRED": "storage_encryption_required",
    "LOG_LEVEL": "log_level",
    "DOCUMENT_RETENTION_DAYS": "document_retention_days",
    "EXTRACTED_TEXT_RETENTION_DAYS": "extracted_text_retention_days",
    "AUDIT_LOG_RETENTION_DAYS": "audit_log_retention_days",
}


def get_settings() -> Settings:
    """FastAPI-compatible dependency for injecting the current settings."""
    return Settings.from_environment()
