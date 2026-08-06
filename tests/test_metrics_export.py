"""Tests for Prometheus export, alert webhook notification, and the alert scheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.monitoring import (
    ALERT_RULES,
    DATABASE_LATENCY,
    PROMETHEUS_CONTENT_TYPE,
    AlertEvaluation,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    MetricsCollector,
    notify_firing_alerts,
    render_prometheus_text,
)


@pytest.fixture(autouse=True)
def _reset_webhook_breaker():
    from boardmatch.monitoring import _ALERT_WEBHOOK_BREAKER

    _ALERT_WEBHOOK_BREAKER.reset()
    yield
    _ALERT_WEBHOOK_BREAKER.reset()


class TestRenderPrometheusText:
    def test_renders_counter(self):
        collector = MetricsCollector()
        collector.increment("requests_total", labels={"method": "GET"})
        text = render_prometheus_text(collector)
        assert "# TYPE requests_total counter" in text
        assert 'requests_total{method="GET"} 1.0' in text

    def test_renders_gauge(self):
        collector = MetricsCollector()
        collector.set_gauge("active_connections", 5.0)
        text = render_prometheus_text(collector)
        assert "# TYPE active_connections gauge" in text
        assert "active_connections 5.0" in text

    def test_renders_histogram_as_count_and_sum(self):
        collector = MetricsCollector()
        collector.observe("latency_ms", 10.0)
        collector.observe("latency_ms", 20.0)
        text = render_prometheus_text(collector)
        assert "latency_ms_count 2" in text
        assert "latency_ms_sum 30.0" in text

    def test_sanitizes_metric_name(self):
        collector = MetricsCollector()
        collector.increment("my.metric-name")
        text = render_prometheus_text(collector)
        assert "my_metric_name" in text

    def test_escapes_label_values(self):
        collector = MetricsCollector()
        collector.increment("errors", labels={"path": 'has "quotes"'})
        text = render_prometheus_text(collector)
        assert '\\"quotes\\"' in text

    def test_empty_collector_renders_empty_string(self):
        collector = MetricsCollector()
        assert render_prometheus_text(collector) == ""

    def test_pii_labels_never_exported(self):
        collector = MetricsCollector()
        collector.increment(
            "test", labels={"email": "user@example.com", "method": "GET"}
        )
        text = render_prometheus_text(collector)
        assert "user@example.com" not in text


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_prometheus_format(self):
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(
            PROMETHEUS_CONTENT_TYPE.split(";")[0]
        )

    def test_metrics_endpoint_reflects_recorded_metrics(self):
        from boardmatch import monitoring

        monitoring.metrics.reset()
        monitoring.metrics.increment("test_counter_for_endpoint")
        client = TestClient(app)
        resp = client.get("/metrics")
        assert "test_counter_for_endpoint" in resp.text


class TestNotifyFiringAlerts:
    def _make_evaluation(
        self, status: AlertStatus, severity=AlertSeverity.WARNING
    ) -> AlertEvaluation:
        rule = AlertRule(
            name="test_rule",
            description="Test",
            severity=severity,
            metric_name=DATABASE_LATENCY,
            threshold=5.0,
        )
        return AlertEvaluation(rule=rule, status=status, current_value=10.0)

    def test_no_firing_alerts_is_noop(self):
        evaluations = [self._make_evaluation(AlertStatus.OK)]
        result = notify_firing_alerts(evaluations, webhook_url=None)
        assert result == []

    def test_firing_without_webhook_logs_only(self, caplog):
        evaluations = [self._make_evaluation(AlertStatus.FIRING)]
        with caplog.at_level("WARNING"):
            result = notify_firing_alerts(evaluations, webhook_url=None)
        assert len(result) == 1
        assert any("Alert firing" in record.message for record in caplog.records)

    @patch("boardmatch.monitoring.requests.post")
    def test_firing_with_webhook_posts_payload(self, mock_post):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        mock_post.return_value = response

        evaluations = [self._make_evaluation(AlertStatus.FIRING)]
        notify_firing_alerts(evaluations, webhook_url="https://example.com/webhook")

        assert mock_post.call_count == 1
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["alerts"][0]["name"] == "test_rule"
        assert "timeout" in kwargs

    @patch("boardmatch.monitoring.requests.post")
    def test_webhook_failure_does_not_raise(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("down")
        evaluations = [self._make_evaluation(AlertStatus.FIRING)]
        # Should not raise even though delivery fails after retries.
        result = notify_firing_alerts(
            evaluations, webhook_url="https://example.com/webhook"
        )
        assert len(result) == 1

    @patch("boardmatch.monitoring.requests.post")
    def test_webhook_retries_transient_failure(self, mock_post):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        mock_post.side_effect = [requests.exceptions.Timeout("slow"), response]

        evaluations = [self._make_evaluation(AlertStatus.FIRING)]
        notify_firing_alerts(evaluations, webhook_url="https://example.com/webhook")
        assert mock_post.call_count == 2


class TestAlertEvaluationLoop:
    def test_loop_evaluates_and_stops_on_event(self):
        import asyncio

        from boardmatch.monitoring import run_alert_evaluation_loop

        async def scenario():
            stop_event = asyncio.Event()
            collector = MetricsCollector()

            async def stopper():
                await asyncio.sleep(0.05)
                stop_event.set()

            with patch("boardmatch.monitoring.evaluate_alerts") as mock_evaluate:
                mock_evaluate.return_value = []
                await asyncio.gather(
                    run_alert_evaluation_loop(
                        interval_seconds=0.01,
                        collector=collector,
                        stop_event=stop_event,
                    ),
                    stopper(),
                )
                assert mock_evaluate.call_count >= 1

        asyncio.run(scenario())


def test_all_alert_rules_still_registered():
    """Sanity check that adding exporters didn't affect existing alert rule definitions."""
    assert len(ALERT_RULES) >= 4
