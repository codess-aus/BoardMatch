"""Tests for AzureDocumentIntelligenceProvider (real SDK call + circuit breaker)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from boardmatch.document_processing import (
    AzureDocumentIntelligenceProvider,
    ExtractedField,
)


class _StubFallback:
    """A fallback provider stub that records the text it was called with."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, document_content: str) -> list[ExtractedField]:
        self.calls.append(document_content)
        return [
            ExtractedField(
                field_name="skills",
                value=["governance"],
                confidence=0.9,
                needs_review=False,
            )
        ]


class TestConfigured:
    def test_not_configured_without_endpoint(self):
        provider = AzureDocumentIntelligenceProvider()
        assert provider.configured() is False

    def test_configured_with_endpoint(self):
        provider = AzureDocumentIntelligenceProvider(
            endpoint="https://example.cognitiveservices.azure.com"
        )
        assert provider.configured() is True


class TestFallbackWhenNotConfigured:
    def test_extract_uses_fallback_directly(self):
        fallback = _StubFallback()
        provider = AzureDocumentIntelligenceProvider(fallback_provider=fallback)
        fields = provider.extract("Jane Smith\n15 years governance")
        assert fields[0].field_name == "skills"
        assert fallback.calls == ["Jane Smith\n15 years governance"]

    def test_default_fallback_is_template_provider(self):
        provider = AzureDocumentIntelligenceProvider()
        fields = provider.extract("Jane Smith\n15 years governance finance. MBA.")
        assert any(f.field_name == "skills" for f in fields)


class TestRealCallSuccess:
    def test_extract_calls_azure_and_maps_via_fallback(self):
        fallback = _StubFallback()
        provider = AzureDocumentIntelligenceProvider(
            endpoint="https://example.cognitiveservices.azure.com",
            api_key="fake-key",
            fallback_provider=fallback,
        )

        mock_result = MagicMock()
        mock_result.content = "Jane Smith\n15 years governance experience"
        mock_poller = MagicMock()
        mock_poller.result.return_value = mock_result
        mock_client = MagicMock()
        mock_client.begin_analyze_document.return_value = mock_poller

        with patch(
            "azure.ai.documentintelligence.DocumentIntelligenceClient",
            return_value=mock_client,
        ):
            fields = provider.extract("raw pdf bytes as text")

        mock_client.begin_analyze_document.assert_called_once()
        args, _ = mock_client.begin_analyze_document.call_args
        assert args[0] == "prebuilt-read"
        # The fallback should receive the *recognized* text from Azure, not
        # the original input.
        assert fallback.calls == ["Jane Smith\n15 years governance experience"]
        assert fields[0].field_name == "skills"

    def test_consecutive_failures_reset_after_success(self):
        provider = AzureDocumentIntelligenceProvider(
            endpoint="https://example.cognitiveservices.azure.com",
            api_key="fake-key",
            failure_threshold=2,
        )
        provider._consecutive_failures = 1

        mock_result = MagicMock()
        mock_result.content = "some text"
        mock_poller = MagicMock()
        mock_poller.result.return_value = mock_result
        mock_client = MagicMock()
        mock_client.begin_analyze_document.return_value = mock_poller

        with patch(
            "azure.ai.documentintelligence.DocumentIntelligenceClient",
            return_value=mock_client,
        ):
            provider.extract("content")

        assert provider._consecutive_failures == 0


class TestCircuitBreaker:
    def test_falls_back_on_single_failure(self):
        fallback = _StubFallback()
        provider = AzureDocumentIntelligenceProvider(
            endpoint="https://example.cognitiveservices.azure.com",
            api_key="fake-key",
            fallback_provider=fallback,
            failure_threshold=3,
        )
        with patch.object(provider, "_analyze", side_effect=RuntimeError("timeout")):
            fields = provider.extract("Jane Smith\ngovernance")
        assert fields[0].field_name == "skills"
        assert fallback.calls == ["Jane Smith\ngovernance"]

    def test_circuit_opens_after_threshold(self):
        fallback = _StubFallback()
        provider = AzureDocumentIntelligenceProvider(
            endpoint="https://example.cognitiveservices.azure.com",
            api_key="fake-key",
            fallback_provider=fallback,
            failure_threshold=2,
        )
        with patch.object(
            provider, "_analyze", side_effect=RuntimeError("timeout")
        ) as mock_analyze:
            provider.extract("content 1")
            provider.extract("content 2")
            assert mock_analyze.call_count == 2
            assert provider._circuit_open_until is not None

            # Circuit is now open — a further call should skip _analyze entirely.
            provider.extract("content 3")
            assert mock_analyze.call_count == 2

    def test_circuit_half_opens_after_reset_window(self):
        provider = AzureDocumentIntelligenceProvider(
            endpoint="https://example.cognitiveservices.azure.com",
            api_key="fake-key",
            failure_threshold=1,
            reset_after_seconds=300,
        )
        with patch.object(provider, "_analyze", side_effect=RuntimeError("timeout")):
            provider.extract("content")
        assert provider._circuit_open_until is not None

        # Simulate time passing beyond the reset window.
        provider._circuit_open_until = provider._circuit_open_until - timedelta(
            seconds=301
        )
        assert provider._circuit_open() is False
        assert provider._circuit_open_until is None
        assert provider._consecutive_failures == 0

    def test_never_raises_to_caller(self):
        provider = AzureDocumentIntelligenceProvider(
            endpoint="https://example.cognitiveservices.azure.com",
            api_key="fake-key",
        )
        with patch.object(provider, "_analyze", side_effect=RuntimeError("boom")):
            # Should not raise despite the underlying SDK call failing.
            fields = provider.extract("Jane Smith\n10 years governance. MBA.")
        assert isinstance(fields, list)
