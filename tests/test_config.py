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
