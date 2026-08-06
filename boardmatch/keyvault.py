"""Azure Key Vault secret loading helpers.

Production secrets (Azure OpenAI keys, future database credentials, storage
keys) can be sourced from Azure Key Vault instead of plain environment
variables. This module is intentionally isolated so that:

- Importing it never fails in local/test environments that do not have
  ``azure-identity``/``azure-keyvault-secrets`` configured or reachable.
- Callers (``boardmatch.config``) only need to call ``fetch_secrets`` with a
  vault URL and the secret names they need; any failure raises a clear
  ``KeyVaultUnavailableError`` that the caller can decide how to handle.

Secret names follow Key Vault's naming convention (letters, numbers, and
dashes only), so environment-style names like ``AZURE_OPENAI_API_KEY`` are
mapped to ``AZURE-OPENAI-API-KEY`` by default.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)


class KeyVaultUnavailableError(RuntimeError):
    """Raised when Key Vault secrets cannot be retrieved."""


def env_name_to_secret_name(env_name: str) -> str:
    """Convert an environment variable style name to a Key Vault secret name.

    Key Vault secret names may only contain alphanumeric characters and
    dashes, so underscores are translated to dashes (e.g.
    ``AZURE_OPENAI_API_KEY`` -> ``AZURE-OPENAI-API-KEY``).
    """
    return env_name.replace("_", "-")


def fetch_secrets(
    vault_url: str,
    secret_env_names: Iterable[str],
    *,
    client: object | None = None,
) -> Mapping[str, str]:
    """Fetch the given secrets (by environment-variable name) from Key Vault.

    Returns a mapping of environment-variable name -> secret value for every
    secret that exists in the vault. Secrets that are not found are silently
    omitted so callers can fall back to plain environment variables.

    Args:
        vault_url: The Key Vault URL, e.g. ``https://my-vault.vault.azure.net``.
        secret_env_names: Environment-variable style names to look up (each is
            translated to a Key Vault secret name via
            :func:`env_name_to_secret_name`).
        client: Optional pre-constructed ``SecretClient``-like object, used by
            tests to inject a mock instead of hitting Azure.

    Raises:
        KeyVaultUnavailableError: If the Azure SDKs are not installed, or if
            authenticating/connecting to Key Vault fails.
    """
    if client is None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError as exc:  # pragma: no cover - exercised via tests with mocks
            raise KeyVaultUnavailableError(
                "azure-identity and azure-keyvault-secrets must be installed "
                "to load secrets from Key Vault"
            ) from exc

        try:
            client = SecretClient(
                vault_url=vault_url, credential=DefaultAzureCredential()
            )
        except Exception as exc:  # pragma: no cover - depends on Azure environment
            raise KeyVaultUnavailableError(
                f"Failed to create Key Vault client for {vault_url}: {exc}"
            ) from exc

    resolved: dict[str, str] = {}
    for env_name in secret_env_names:
        vault_entry_name = env_name_to_secret_name(env_name)
        try:
            entry = client.get_secret(vault_entry_name)
        except Exception:  # noqa: BLE001 - entry may not exist or vault may reject access
            # Note: vault_entry_name is only the Key Vault entry's *name* (e.g.
            # "AZURE-OPENAI-API-KEY"), never its value, so this is safe to log.
            logger.debug(
                "Key Vault entry %s not available; falling back to env",
                vault_entry_name,
            )
            continue
        value = getattr(entry, "value", None)
        if value:
            resolved[env_name] = value
    return resolved
