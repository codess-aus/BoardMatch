import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from boardmatch.api import app
from boardmatch.config import AppEnvironment, Settings


def test_valid_production_configuration_loads():
    settings = Settings.from_environment(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql://db.example/boardmatch",
            "AUTH_ISSUER": "https://login.example.com/tenant",
            "AUTH_AUDIENCE": "api://boardmatch",
            "AZURE_OPENAI_ENDPOINT": "https://boardmatch.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "secret-api-key",
            "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
            "AZURE_STORAGE_ACCOUNT": "boardmatchdata",
            "LOG_LEVEL": "WARNING",
        }
    )

    assert settings.app_env is AppEnvironment.PRODUCTION
    assert settings.log_level == "WARNING"
    assert settings.azure_openai_api_key is not None
    assert settings.azure_openai_api_key.get_secret_value() == "secret-api-key"


def test_production_config_example_matches_documentation():
    """Mirrors the example values in docs/production-config-example.md.

    Keep this in sync with that document: if either changes, the other
    should be updated so the documented example remains provably valid.
    """
    settings = Settings.from_environment(
        {
            "APP_ENV": "production",
            "DATABASE_URL": (
                "postgresql+psycopg://boardmatch_app:REPLACE_ME@"
                "boardmatch-prod.postgres.database.azure.com:5432/boardmatch"
                "?sslmode=require"
            ),
            "LOG_LEVEL": "WARNING",
            "AUTH_ISSUER": (
                "https://login.microsoftonline.com/"
                "00000000-0000-0000-0000-000000000000/v2.0"
            ),
            "AUTH_AUDIENCE": "api://boardmatch-prod",
            "AZURE_OPENAI_ENDPOINT": "https://boardmatch-prod.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "REPLACE_ME_OR_SOURCE_FROM_KEY_VAULT",
            "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
            "AZURE_STORAGE_ACCOUNT": "boardmatchprodstorage",
            "AZURE_STORAGE_CONTAINER": "documents",
            "STORAGE_ENCRYPTION_REQUIRED": "true",
            "AZURE_DOC_INTELLIGENCE_ENDPOINT": (
                "https://boardmatch-prod-di.cognitiveservices.azure.com"
            ),
            "AZURE_DOC_INTELLIGENCE_KEY": "REPLACE_ME_OR_SOURCE_FROM_KEY_VAULT",
            "MS_GRAPH_CLIENT_ID": "00000000-0000-0000-0000-000000000000",
            "MS_GRAPH_CLIENT_SECRET": "REPLACE_ME_OR_SOURCE_FROM_KEY_VAULT",
            "MS_GRAPH_TENANT_ID": "00000000-0000-0000-0000-000000000000",
            "MS_GRAPH_REDIRECT_URI": (
                "https://boardmatch.example.com/api/v1/integrations/graph/callback"
            ),
            "KEY_VAULT_URL": "https://boardmatch-prod-kv.vault.azure.net",
            "CORS_ALLOWED_ORIGINS": "https://boardmatch.example.com",
            "RATE_LIMIT_MAX_REQUESTS": "30",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "DOCUMENT_RETENTION_DAYS": "365",
            "EXTRACTED_TEXT_RETENTION_DAYS": "90",
            "AUDIT_LOG_RETENTION_DAYS": "90",
            "ALERT_WEBHOOK_URL": "https://REPLACE_ME.example.com/incoming-webhook",
            "ALERT_EVALUATION_INTERVAL_SECONDS": "60",
        }
    )

    assert settings.app_env is AppEnvironment.PRODUCTION
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.azure_storage_account == "boardmatchprodstorage"
    assert settings.key_vault_url == "https://boardmatch-prod-kv.vault.azure.net"


def test_missing_production_settings_fail_without_leaking_secrets():
    with pytest.raises(ValidationError) as exc_info:
        Settings.from_environment(
            {
                "APP_ENV": "production",
                "DATABASE_URL": "postgresql://db.example/boardmatch",
                "AZURE_OPENAI_API_KEY": "secret-api-key",
            }
        )

    message = str(exc_info.value)
    assert "AUTH_ISSUER" in message
    assert "secret-api-key" not in message


def test_test_environment_uses_safe_defaults():
    settings = Settings.from_environment({"APP_ENV": "test"})

    assert settings.database_url == "sqlite:///./boardmatch.db"
    assert settings.azure_openai_api_key is None


def test_production_rejects_insecure_settings():
    with pytest.raises(ValidationError, match="must not use SQLite"):
        Settings.from_environment(
            {
                "APP_ENV": "production",
                "AUTH_ISSUER": "https://login.example.com/tenant",
                "AUTH_AUDIENCE": "api://boardmatch",
                "AZURE_OPENAI_ENDPOINT": "https://boardmatch.openai.azure.com",
                "AZURE_OPENAI_API_KEY": "secret-api-key",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
                "AZURE_STORAGE_ACCOUNT": "boardmatchdata",
            }
        )


def test_application_starts_with_local_configuration(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")

    with TestClient(app) as client:
        assert client.get("/api/candidate").status_code == 200


def test_cors_allowed_origins_parses_comma_separated_string():
    settings = Settings.from_environment(
        {
            "APP_ENV": "test",
            "CORS_ALLOWED_ORIGINS": "https://a.example.com, https://b.example.com",
        }
    )

    assert settings.cors_allowed_origins == (
        "https://a.example.com",
        "https://b.example.com",
    )


def test_cors_allowed_origins_defaults_to_empty():
    settings = Settings.from_environment({"APP_ENV": "test"})

    assert settings.cors_allowed_origins == ()


def test_from_key_vault_and_environment_falls_back_without_vault_url():
    settings = Settings.from_key_vault_and_environment(
        {"APP_ENV": "test", "AZURE_OPENAI_API_KEY": "env-key"}
    )

    assert settings.azure_openai_api_key is not None
    assert settings.azure_openai_api_key.get_secret_value() == "env-key"


def test_from_key_vault_and_environment_prefers_vault_secret():
    class _FakeSecret:
        def __init__(self, value: str) -> None:
            self.value = value

    class _FakeClient:
        def get_secret(self, name: str) -> _FakeSecret:
            if name == "AZURE-OPENAI-API-KEY":
                return _FakeSecret("vault-key")
            raise KeyError(name)

    settings = Settings.from_key_vault_and_environment(
        {
            "APP_ENV": "test",
            "KEY_VAULT_URL": "https://boardmatch-kv.vault.azure.net",
            "AZURE_OPENAI_API_KEY": "env-key",
        },
        key_vault_client=_FakeClient(),
    )

    assert settings.azure_openai_api_key is not None
    assert settings.azure_openai_api_key.get_secret_value() == "vault-key"


def test_from_key_vault_and_environment_falls_back_when_secret_missing():
    class _FakeClient:
        def get_secret(self, name: str):
            raise KeyError(name)

    settings = Settings.from_key_vault_and_environment(
        {
            "APP_ENV": "test",
            "KEY_VAULT_URL": "https://boardmatch-kv.vault.azure.net",
            "AZURE_OPENAI_API_KEY": "env-key",
        },
        key_vault_client=_FakeClient(),
    )

    assert settings.azure_openai_api_key is not None
    assert settings.azure_openai_api_key.get_secret_value() == "env-key"
