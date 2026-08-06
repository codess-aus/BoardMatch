"""Tests for coach.py's Azure OpenAI call resilience (retry + circuit breaker)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from boardmatch import coach


class _FakeAPIConnectionError(Exception):
    pass


class _FakeAPITimeoutError(Exception):
    pass


@pytest.fixture(autouse=True)
def _reset_breaker():
    coach._AZURE_OPENAI_BREAKER.reset()
    yield
    coach._AZURE_OPENAI_BREAKER.reset()


@pytest.fixture()
def configured_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4")


def _install_fake_openai(create_side_effect):
    """Install a fake `openai` module in sys.modules with a scripted create() call."""
    fake_module = types.ModuleType("openai")

    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content="Generated content"))]

    mock_create = MagicMock(side_effect=create_side_effect)

    class _FakeAzureOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = MagicMock()
            self.chat.completions.create = mock_create

    fake_module.AzureOpenAI = _FakeAzureOpenAI
    fake_module.APIConnectionError = _FakeAPIConnectionError
    fake_module.APITimeoutError = _FakeAPITimeoutError
    sys.modules["openai"] = fake_module
    return mock_create, completion


class TestCallAzureOpenAI:
    def test_retries_transient_error_then_succeeds(self, configured_env, monkeypatch):
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(content="Generated content"))]
        mock_create, _ = _install_fake_openai(
            [_FakeAPIConnectionError("blip"), completion]
        )
        try:
            result = coach._call_azure_openai("draft me a CV")
            assert result == "Generated content"
            assert mock_create.call_count == 2
        finally:
            sys.modules.pop("openai", None)

    def test_generate_falls_back_to_none_after_repeated_failures(self, configured_env):
        mock_create, _ = _install_fake_openai(_FakeAPIConnectionError("down"))
        try:
            # _generate() catches everything and falls back to template mode.
            result = coach._generate("draft me a CV")
            assert result is None
            assert mock_create.call_count == 3  # max_attempts=3
        finally:
            sys.modules.pop("openai", None)

    def test_not_configured_returns_none_without_calling_openai(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
        assert coach._generate("draft me a CV") is None

    def test_breaker_opens_after_repeated_failures(self, configured_env):
        _install_fake_openai(_FakeAPIConnectionError("down"))
        try:
            from boardmatch.resilience import CircuitBreakerOpenError

            coach._AZURE_OPENAI_BREAKER.failure_threshold = 1
            with pytest.raises(ConnectionError):
                coach._call_azure_openai("draft me a CV")

            with pytest.raises(CircuitBreakerOpenError):
                coach._call_azure_openai("draft me a CV")
        finally:
            coach._AZURE_OPENAI_BREAKER.failure_threshold = 5
            sys.modules.pop("openai", None)
