"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
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
    azure_storage_container: str = "documents"
    storage_encryption_required: bool = True
    azure_doc_intelligence_endpoint: str | None = None
    azure_doc_intelligence_key: SecretStr | None = None
    ms_graph_client_id: str | None = None
    ms_graph_client_secret: SecretStr | None = None
    ms_graph_tenant_id: str = "common"
    ms_graph_redirect_uri: str | None = None
    document_retention_days: int = 365
    extracted_text_retention_days: int = 90
    audit_log_retention_days: int = 90
    network_data_retention_days: int = 365
    log_level: str = "INFO"
    key_vault_url: str | None = None
    cors_allowed_origins: tuple[str, ...] = ()
    rate_limit_max_requests: int = 30
    rate_limit_window_seconds: int = 60
    alert_webhook_url: str | None = None
    alert_evaluation_interval_seconds: float = 60.0

    @field_validator(
        "auth_issuer",
        "auth_audience",
        "azure_openai_endpoint",
        "azure_openai_api_key",
        "azure_openai_deployment",
        "azure_storage_account",
        "azure_doc_intelligence_endpoint",
        "azure_doc_intelligence_key",
        "ms_graph_client_id",
        "ms_graph_client_secret",
        "ms_graph_redirect_uri",
        "key_vault_url",
        "alert_webhook_url",
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

    @field_validator(
        "auth_issuer",
        "azure_openai_endpoint",
        "azure_doc_intelligence_endpoint",
        "key_vault_url",
        "alert_webhook_url",
    )
    @classmethod
    def validate_optional_url(cls, value: str | None) -> str | None:
        if value is not None and urlparse(value).scheme not in {"http", "https"}:
            raise ValueError("must be an HTTP(S) URL")
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(
                origin.strip() for origin in value.split(",") if origin.strip()
            )
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
    def validate_production_settings(self) -> Settings:
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
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load settings without requiring process-wide environment mutation in tests."""
        source = os.environ if environ is None else environ
        values = {
            field: source[name]
            for name, field in _ENVIRONMENT_FIELDS.items()
            if name in source
        }
        return cls(**values)

    @classmethod
    def from_key_vault_and_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        key_vault_client: object | None = None,
    ) -> Settings:
        """Load settings, preferring Azure Key Vault for secret fields.

        If ``KEY_VAULT_URL`` is configured (via the environment or
        ``environ``), secret-bearing fields (``AZURE_OPENAI_API_KEY`` and any
        other names in :data:`KEY_VAULT_SECRET_ENV_NAMES`) are fetched from
        Key Vault using ``DefaultAzureCredential``. Any secret not found in
        the vault falls back to the plain environment variable, so local and
        test environments without Key Vault access are unaffected.

        Raises:
            KeyVaultUnavailableError: If ``KEY_VAULT_URL`` is set but the
                Azure SDKs are unavailable or the vault cannot be reached.
        """
        source = os.environ if environ is None else environ
        vault_url = source.get("KEY_VAULT_URL") or source.get("AZURE_KEY_VAULT_NAME")
        if not vault_url:
            return cls.from_environment(environ)

        from .keyvault import fetch_secrets

        vault_secrets = fetch_secrets(
            vault_url, KEY_VAULT_SECRET_ENV_NAMES, client=key_vault_client
        )
        merged = dict(source)
        merged.update(vault_secrets)
        return cls.from_environment(merged)


_ENVIRONMENT_FIELDS = {
    "APP_ENV": "app_env",
    "DATABASE_URL": "database_url",
    "AUTH_ISSUER": "auth_issuer",
    "AUTH_AUDIENCE": "auth_audience",
    "AZURE_OPENAI_ENDPOINT": "azure_openai_endpoint",
    "AZURE_OPENAI_API_KEY": "azure_openai_api_key",
    "AZURE_OPENAI_DEPLOYMENT": "azure_openai_deployment",
    "AZURE_STORAGE_ACCOUNT": "azure_storage_account",
    "AZURE_STORAGE_CONTAINER": "azure_storage_container",
    "STORAGE_ENCRYPTION_REQUIRED": "storage_encryption_required",
    "AZURE_DOC_INTELLIGENCE_ENDPOINT": "azure_doc_intelligence_endpoint",
    "AZURE_DOC_INTELLIGENCE_KEY": "azure_doc_intelligence_key",
    "MS_GRAPH_CLIENT_ID": "ms_graph_client_id",
    "MS_GRAPH_CLIENT_SECRET": "ms_graph_client_secret",
    "MS_GRAPH_TENANT_ID": "ms_graph_tenant_id",
    "MS_GRAPH_REDIRECT_URI": "ms_graph_redirect_uri",
    "LOG_LEVEL": "log_level",
    "DOCUMENT_RETENTION_DAYS": "document_retention_days",
    "EXTRACTED_TEXT_RETENTION_DAYS": "extracted_text_retention_days",
    "AUDIT_LOG_RETENTION_DAYS": "audit_log_retention_days",
    "KEY_VAULT_URL": "key_vault_url",
    "CORS_ALLOWED_ORIGINS": "cors_allowed_origins",
    "RATE_LIMIT_MAX_REQUESTS": "rate_limit_max_requests",
    "RATE_LIMIT_WINDOW_SECONDS": "rate_limit_window_seconds",
    "ALERT_WEBHOOK_URL": "alert_webhook_url",
    "ALERT_EVALUATION_INTERVAL_SECONDS": "alert_evaluation_interval_seconds",
}

# Environment-variable style names of secrets that may be sourced from Azure
# Key Vault instead of plain environment variables. Extend this tuple as
# future secrets (database credentials, storage keys) are added.
KEY_VAULT_SECRET_ENV_NAMES: tuple[str, ...] = ("AZURE_OPENAI_API_KEY",)


def get_settings() -> Settings:
    """FastAPI-compatible dependency for injecting the current settings."""
    return Settings.from_environment()
