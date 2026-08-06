"""Tests for the retry-with-backoff wrapper layered on top of
AzureDocumentIntelligenceProvider._analyze() (the real SDK call site).

The provider's own circuit breaker (open/half-open/closed, fallback
behavior) is covered by tests/test_azure_document_intelligence.py. These
tests only exercise the additional retry-with-backoff layer from
boardmatch/resilience.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from boardmatch.document_processing import AzureDocumentIntelligenceProvider


def _provider(**kwargs) -> AzureDocumentIntelligenceProvider:
    return AzureDocumentIntelligenceProvider(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="test-key",
        **kwargs,
    )


class TestAnalyzeRetry:
    def test_retries_transient_error_then_succeeds(self):
        provider = _provider()

        mock_result = MagicMock()
        mock_result.content = "Jane Smith\n15 years governance experience"
        mock_poller = MagicMock()
        mock_poller.result.return_value = mock_result
        mock_client = MagicMock()
        mock_client.begin_analyze_document.side_effect = [
            ConnectionError("blip"),
            mock_poller,
        ]

        with patch(
            "azure.ai.documentintelligence.DocumentIntelligenceClient",
            return_value=mock_client,
        ):
            text = provider._analyze("raw content")

        assert text == "Jane Smith\n15 years governance experience"
        assert mock_client.begin_analyze_document.call_count == 2

    def test_gives_up_after_max_attempts(self):
        provider = _provider()

        mock_client = MagicMock()
        mock_client.begin_analyze_document.side_effect = ConnectionError("down")

        with (
            patch(
                "azure.ai.documentintelligence.DocumentIntelligenceClient",
                return_value=mock_client,
            ),
            pytest.raises(ConnectionError),
        ):
            provider._analyze("raw content")

        assert mock_client.begin_analyze_document.call_count == 3  # max_attempts=3

    def test_extract_falls_back_after_retries_exhausted(self):
        """extract() still falls back gracefully once retries inside
        _analyze() are exhausted — the outer circuit breaker in extract()
        catches the final exception, same as any other failure mode."""
        provider = _provider(failure_threshold=5)

        mock_client = MagicMock()
        mock_client.begin_analyze_document.side_effect = ConnectionError("down")

        with patch(
            "azure.ai.documentintelligence.DocumentIntelligenceClient",
            return_value=mock_client,
        ):
            fields = provider.extract("Jane Smith\n10 years governance. MBA.")

        assert isinstance(fields, list)
        assert provider._consecutive_failures == 1
        assert mock_client.begin_analyze_document.call_count == 3

    def test_non_transient_error_is_not_retried(self):
        provider = _provider()

        mock_client = MagicMock()
        mock_client.begin_analyze_document.side_effect = ValueError("bad request")

        with (
            patch(
                "azure.ai.documentintelligence.DocumentIntelligenceClient",
                return_value=mock_client,
            ),
            pytest.raises(ValueError),
        ):
            provider._analyze("raw content")

        assert mock_client.begin_analyze_document.call_count == 1
