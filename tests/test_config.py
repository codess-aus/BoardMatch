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
