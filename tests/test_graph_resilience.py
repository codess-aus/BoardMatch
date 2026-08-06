"""Tests for the retry-with-backoff + circuit breaker wrapper layered on top
of boardmatch.integrations' real Microsoft Graph call sites
(exchange_code_for_token / fetch_graph_people).

Route-level behavior (OAuth callback, network sync, fallback to
simulated/fixture data) is covered by tests/test_integrations.py and
tests/test_network_api.py. These tests only exercise the additional
retry/circuit-breaker layer from boardmatch/resilience.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from boardmatch.integrations import (
    GraphApiError,
    GraphTokenExchangeError,
    exchange_code_for_token,
    fetch_graph_people,
)


@pytest.fixture(autouse=True)
def _reset_breaker():
    from boardmatch.integrations import _GRAPH_BREAKER

    _GRAPH_BREAKER.reset()
    original_threshold = _GRAPH_BREAKER.failure_threshold
    yield
    _GRAPH_BREAKER.reset()
    _GRAPH_BREAKER.failure_threshold = original_threshold


def _token_kwargs(**overrides):
    kwargs = {
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "code": "auth-code",
        "redirect_uri": "https://example.com/callback",
        "scopes": ["User.Read"],
    }
    kwargs.update(overrides)
    return kwargs


class TestExchangeCodeForTokenRetry:
    @patch("boardmatch.integrations.requests.post")
    def test_retries_transient_error_then_succeeds(self, mock_post):
        ok_response = MagicMock()
        ok_response.raise_for_status = MagicMock()
        ok_response.json.return_value = {"access_token": "real-token"}
        mock_post.side_effect = [requests.exceptions.Timeout("slow"), ok_response]

        token = exchange_code_for_token(**_token_kwargs())
        assert token == "real-token"
        assert mock_post.call_count == 2

    @patch("boardmatch.integrations.requests.post")
    def test_gives_up_after_max_attempts_raises_token_exchange_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("down")

        with pytest.raises(GraphTokenExchangeError):
            exchange_code_for_token(**_token_kwargs())
        assert mock_post.call_count == 3  # max_attempts=3

    @patch("boardmatch.integrations.requests.post")
    def test_call_signature_preserved(self, mock_post):
        ok_response = MagicMock()
        ok_response.raise_for_status = MagicMock()
        ok_response.json.return_value = {"access_token": "tok"}
        mock_post.return_value = ok_response

        exchange_code_for_token(**_token_kwargs())
        args, kwargs = mock_post.call_args
        assert args[0] == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        assert kwargs["data"]["client_id"] == "client-id"
        assert "timeout" in kwargs


class TestFetchGraphPeopleRetry:
    @patch("boardmatch.integrations.requests.get")
    def test_retries_transient_error_then_succeeds(self, mock_get):
        ok_response = MagicMock()
        ok_response.raise_for_status = MagicMock()
        ok_response.json.return_value = {"value": [{"displayName": "Alice"}]}
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("down"),
            ok_response,
        ]

        people = fetch_graph_people("fake-token")
        assert people == [{"displayName": "Alice"}]
        assert mock_get.call_count == 2

    @patch("boardmatch.integrations.requests.get")
    def test_gives_up_after_max_attempts_raises_graph_api_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("down")

        with pytest.raises(GraphApiError):
            fetch_graph_people("fake-token")
        assert mock_get.call_count == 3  # max_attempts=3

    @patch("boardmatch.integrations.requests.get")
    def test_call_signature_preserved(self, mock_get):
        ok_response = MagicMock()
        ok_response.raise_for_status = MagicMock()
        ok_response.json.return_value = {"value": []}
        mock_get.return_value = ok_response

        fetch_graph_people("fake-token")
        args, kwargs = mock_get.call_args
        assert args[0] == "https://graph.microsoft.com/v1.0/me/people"
        assert kwargs["headers"]["Authorization"] == "Bearer fake-token"
        assert "timeout" in kwargs


class TestCircuitBreakerIntegration:
    @patch("boardmatch.integrations.requests.get")
    def test_breaker_opens_after_repeated_failures(self, mock_get):
        from boardmatch.integrations import _GRAPH_BREAKER

        mock_get.side_effect = requests.exceptions.ConnectionError("down")
        _GRAPH_BREAKER.failure_threshold = 2

        for _ in range(2):
            with pytest.raises(GraphApiError):
                fetch_graph_people("fake-token")

        # Once open, the breaker rejects before even attempting the call;
        # fetch_graph_people's broad except still surfaces this as a
        # GraphApiError (consistent with all its other failure modes), but
        # no further HTTP calls are made.
        call_count_before = mock_get.call_count
        with pytest.raises(GraphApiError):
            fetch_graph_people("fake-token")
        assert mock_get.call_count == call_count_before
