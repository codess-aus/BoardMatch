"""Tests for Azure Key Vault secret loading."""

from __future__ import annotations

import pytest

from boardmatch.keyvault import (
    KeyVaultUnavailableError,
    env_name_to_secret_name,
    fetch_secrets,
)


class _FakeSecret:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeSecretClient:
    """Mimics azure.keyvault.secrets.SecretClient.get_secret."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, name: str) -> _FakeSecret:
        if name not in self._secrets:
            raise KeyError(name)
        return _FakeSecret(self._secrets[name])


def test_env_name_to_secret_name_replaces_underscores():
    assert env_name_to_secret_name("AZURE_OPENAI_API_KEY") == "AZURE-OPENAI-API-KEY"


def test_fetch_secrets_returns_found_values_using_mock_client():
    client = _FakeSecretClient({"AZURE-OPENAI-API-KEY": "super-secret-value"})

    result = fetch_secrets(
        "https://fake-vault.vault.azure.net",
        ["AZURE_OPENAI_API_KEY"],
        client=client,
    )

    assert result == {"AZURE_OPENAI_API_KEY": "super-secret-value"}


def test_fetch_secrets_omits_missing_secrets():
    client = _FakeSecretClient({})

    result = fetch_secrets(
        "https://fake-vault.vault.azure.net",
        ["AZURE_OPENAI_API_KEY", "DATABASE_PASSWORD"],
        client=client,
    )

    assert result == {}


def test_fetch_secrets_raises_when_azure_sdk_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name in {"azure.identity", "azure.keyvault.secrets"}:
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(KeyVaultUnavailableError):
        fetch_secrets("https://fake-vault.vault.azure.net", ["AZURE_OPENAI_API_KEY"])
