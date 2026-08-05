"""Tests for boardmatch.monitoring — metrics, alerts, structured logging, and PII redaction."""

from __future__ import annotations

import logging

import pytest

from boardmatch.monitoring import (
    ALERT_RULES,
    DATABASE_LATENCY,
    HTTP_ERROR_COUNT,
    HTTP_REQUEST_DURATION,
    INGESTION_FAILURE_COUNT,
    INGESTION_SUCCESS_COUNT,
    STALE_OPPORTUNITY_COUNT,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    MetricsCollector,
    StructuredFormatter,
    configure_structured_logging,
    evaluate_alerts,
    record_database_latency,
    record_ingestion_result,
    record_request_duration,
    record_stale_opportunities,
    redact_pii,
    metrics,
)


# ---------------------------------------------------------------------------
# Metric emission tests
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    """Tests for the MetricsCollector class."""

    def setup_method(self) -> None:
        self.collector = MetricsCollector()

    def test_increment_counter(self) -> None:
        self.collector.increment("test_counter")
        assert self.collector.get_counter("test_counter") == 1.0

    def test_increment_counter_with_value(self) -> None:
        self.collector.increment("test_counter", value=5.0)
        assert self.collector.get_counter("test_counter") == 5.0

    def test_increment_counter_accumulates(self) -> None:
        self.collector.increment("test_counter")
        self.collector.increment("test_counter")
        self.collector.increment("test_counter")
        assert self.collector.get_counter("test_counter") == 3.0

    def test_increment_with_labels(self) -> None:
        self.collector.increment("errors", labels={"status_code": "500"})
        self.collector.increment("errors", labels={"status_code": "404"})
        assert self.collector.get_counter("errors", labels={"status_code": "500"}) == 1.0
        assert self.collector.get_counter("errors", labels={"status_code": "404"}) == 1.0

    def test_observe_histogram(self) -> None:
        self.collector.observe("latency", 10.5)
        self.collector.observe("latency", 20.3)
        values = self.collector.get_histogram("latency")
        assert values == [10.5, 20.3]

    def test_set_gauge(self) -> None:
        self.collector.set_gauge("active_connections", 5.0)
        assert self.collector.get_gauge("active_connections") == 5.0
        self.collector.set_gauge("active_connections", 3.0)
        assert self.collector.get_gauge("active_connections") == 3.0

    def test_get_samples(self) -> None:
        self.collector.increment("counter_a")
        self.collector.observe("hist_b", 1.0)
        all_samples = self.collector.get_samples()
        assert len(all_samples) == 2
        filtered = self.collector.get_samples("counter_a")
        assert len(filtered) == 1
        assert filtered[0].name == "counter_a"

    def test_reset_clears_all(self) -> None:
        self.collector.increment("c")
        self.collector.observe("h", 1.0)
        self.collector.set_gauge("g", 2.0)
        self.collector.reset()
        assert self.collector.get_counter("c") == 0.0
        assert self.collector.get_histogram("h") == []
        assert self.collector.get_gauge("g") == 0.0
        assert self.collector.get_samples() == []

    def test_pii_labels_blocked(self) -> None:
        """Labels containing PII keys are silently dropped."""
        self.collector.increment(
            "test",
            labels={"method": "GET", "email": "user@example.com", "phone": "555-1234"},
        )
        assert self.collector.get_counter("test", labels={"method": "GET"}) == 1.0
        samples = self.collector.get_samples("test")
        assert len(samples) == 1
        assert "email" not in samples[0].labels
        assert "phone" not in samples[0].labels

    def test_missing_counter_returns_zero(self) -> None:
        assert self.collector.get_counter("nonexistent") == 0.0

    def test_missing_gauge_returns_zero(self) -> None:
        assert self.collector.get_gauge("nonexistent") == 0.0


# ---------------------------------------------------------------------------
# Error log redaction tests
# ---------------------------------------------------------------------------


class TestPIIRedaction:
    """Tests for PII redaction in log messages."""

    def test_redact_email(self) -> None:
        msg = "User john.doe@example.com submitted a request"
        result = redact_pii(msg)
        assert "john.doe@example.com" not in result
        assert "[EMAIL_REDACTED]" in result

    def test_redact_phone(self) -> None:
        msg = "Contact number: 555-123-4567"
        result = redact_pii(msg)
        assert "555-123-4567" not in result
        assert "[PHONE_REDACTED]" in result

    def test_redact_ssn(self) -> None:
        msg = "SSN is 123-45-6789"
        result = redact_pii(msg)
        assert "123-45-6789" not in result
        assert "[SSN_REDACTED]" in result

    def test_redact_multiple_pii(self) -> None:
        msg = "Email: test@test.com, Phone: 555.123.4567"
        result = redact_pii(msg)
        assert "test@test.com" not in result
        assert "555.123.4567" not in result

    def test_no_redaction_needed(self) -> None:
        msg = "GET /health/live 200 1.2ms"
        result = redact_pii(msg)
        assert result == msg

    def test_structured_formatter_redacts_pii(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="User %s logged in",
            args=("admin@company.com",),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "admin@company.com" not in output
        assert "EMAIL_REDACTED" in output


# ---------------------------------------------------------------------------
# Alert rule validation tests
# ---------------------------------------------------------------------------


class TestAlertRules:
    """Tests for alert rule definitions and evaluation."""

    def test_all_alert_rules_have_required_fields(self) -> None:
        for rule in ALERT_RULES:
            assert rule.name, "Alert rule must have a name"
            assert rule.description, "Alert rule must have a description"
            assert rule.severity in (AlertSeverity.WARNING, AlertSeverity.CRITICAL)
            assert rule.metric_name, "Alert rule must reference a metric"
            assert rule.window_seconds > 0, "Alert window must be positive"

    def test_database_failure_alert_exists(self) -> None:
        names = [r.name for r in ALERT_RULES]
        assert "database_failure" in names

    def test_ingestion_failure_alert_exists(self) -> None:
        names = [r.name for r in ALERT_RULES]
        assert "ingestion_failure_repeated" in names

    def test_stale_data_alert_exists(self) -> None:
        names = [r.name for r in ALERT_RULES]
        assert "stale_opportunity_data" in names

    def test_auth_spike_alert_exists(self) -> None:
        names = [r.name for r in ALERT_RULES]
        assert "auth_failure_spike" in names

    def test_alert_rule_evaluate_ok(self) -> None:
        collector = MetricsCollector()
        rule = AlertRule(
            name="test_rule",
            description="Test",
            severity=AlertSeverity.WARNING,
            metric_name="test_metric",
            threshold=5.0,
            comparison="gt",
        )
        assert rule.evaluate(collector) == AlertStatus.OK

    def test_alert_rule_evaluate_firing(self) -> None:
        collector = MetricsCollector()
        collector.increment("test_metric", value=10.0)
        rule = AlertRule(
            name="test_rule",
            description="Test",
            severity=AlertSeverity.WARNING,
            metric_name="test_metric",
            threshold=5.0,
            comparison="gt",
        )
        assert rule.evaluate(collector) == AlertStatus.FIRING

    def test_alert_rule_gte_comparison(self) -> None:
        collector = MetricsCollector()
        collector.increment("test_metric", value=5.0)
        rule = AlertRule(
            name="test_rule",
            description="Test",
            severity=AlertSeverity.WARNING,
            metric_name="test_metric",
            threshold=5.0,
            comparison="gte",
        )
        assert rule.evaluate(collector) == AlertStatus.FIRING

    def test_evaluate_alerts_returns_results(self) -> None:
        collector = MetricsCollector()
        results = evaluate_alerts(collector)
        assert len(results) == len(ALERT_RULES)
        for result in results:
            assert result.status in (AlertStatus.OK, AlertStatus.FIRING)


# ---------------------------------------------------------------------------
# Ingestion failure alert tests
# ---------------------------------------------------------------------------


class TestIngestionFailureAlert:
    """Tests that ingestion failures trigger the alert."""

    def setup_method(self) -> None:
        self.collector = MetricsCollector()

    def test_single_failure_does_not_fire(self) -> None:
        direct_rule = AlertRule(
            name="ingestion_failure_repeated",
            description="Test",
            severity=AlertSeverity.CRITICAL,
            metric_name=INGESTION_FAILURE_COUNT,
            threshold=2.0,
            comparison="gte",
        )
        self.collector.increment(INGESTION_FAILURE_COUNT, value=1.0)
        assert direct_rule.evaluate(self.collector) == AlertStatus.OK

    def test_multiple_failures_fires(self) -> None:
        self.collector.increment(INGESTION_FAILURE_COUNT, value=3.0)
        rule = AlertRule(
            name="ingestion_failure_repeated",
            description="Test",
            severity=AlertSeverity.CRITICAL,
            metric_name=INGESTION_FAILURE_COUNT,
            threshold=2.0,
            comparison="gte",
        )
        assert rule.evaluate(self.collector) == AlertStatus.FIRING

    def test_record_ingestion_result_success(self) -> None:
        metrics.reset()
        record_ingestion_result("gov_source", success=True)
        assert metrics.get_counter(INGESTION_SUCCESS_COUNT, labels={"source_key": "gov_source"}) == 1.0

    def test_record_ingestion_result_failure(self) -> None:
        metrics.reset()
        record_ingestion_result("gov_source", success=False)
        assert metrics.get_counter(INGESTION_FAILURE_COUNT, labels={"source_key": "gov_source"}) == 1.0


# ---------------------------------------------------------------------------
# Database readiness failure tests
# ---------------------------------------------------------------------------


class TestDatabaseReadinessFailure:
    """Tests for database latency alerting."""

    def setup_method(self) -> None:
        self.collector = MetricsCollector()

    def test_normal_latency_does_not_fire(self) -> None:
        rule = next(r for r in ALERT_RULES if r.name == "database_failure")
        assert rule.evaluate(self.collector) == AlertStatus.OK

    def test_high_latency_fires(self) -> None:
        self.collector.increment(DATABASE_LATENCY, value=6000.0)
        rule = next(r for r in ALERT_RULES if r.name == "database_failure")
        assert rule.evaluate(self.collector) == AlertStatus.FIRING

    def test_record_database_latency(self) -> None:
        metrics.reset()
        record_database_latency(25.0)
        samples = metrics.get_samples(DATABASE_LATENCY)
        assert len(samples) == 1
        assert samples[0].value == 25.0


# ---------------------------------------------------------------------------
# Integration: request metrics via helper
# ---------------------------------------------------------------------------


class TestRequestMetrics:
    """Tests for request-level metric recording."""

    def setup_method(self) -> None:
        metrics.reset()

    def test_record_request_duration_success(self) -> None:
        record_request_duration("GET", "/api/v1/health", 200, 15.5)
        samples = metrics.get_samples(HTTP_REQUEST_DURATION)
        assert len(samples) == 1
        assert samples[0].value == 15.5
        assert samples[0].labels["method"] == "GET"

    def test_record_request_duration_error_increments_error_count(self) -> None:
        record_request_duration("POST", "/api/v1/data", 500, 100.0)
        assert metrics.get_counter(HTTP_ERROR_COUNT, labels={"status_code": "500", "method": "POST"}) == 1.0

    def test_record_request_2xx_does_not_increment_error_count(self) -> None:
        record_request_duration("GET", "/api/v1/ok", 200, 5.0)
        assert metrics.get_counter(HTTP_ERROR_COUNT, labels={"status_code": "200", "method": "GET"}) == 0.0

    def test_stale_opportunities_gauge(self) -> None:
        record_stale_opportunities(5)
        assert metrics.get_gauge(STALE_OPPORTUNITY_COUNT) == 5.0
        record_stale_opportunities(0)
        assert metrics.get_gauge(STALE_OPPORTUNITY_COUNT) == 0.0


# ---------------------------------------------------------------------------
# Structured logging configuration
# ---------------------------------------------------------------------------


class TestStructuredLogging:
    """Tests for structured logging setup."""

    def test_configure_structured_logging(self) -> None:
        configure_structured_logging("WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert len(root.handlers) >= 1
        handler = root.handlers[0]
        assert isinstance(handler.formatter, StructuredFormatter)
        # Reset to avoid affecting other tests
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_structured_formatter_output_is_json_like(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Hello world",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        assert output.startswith("{")
        assert output.endswith("}")
        assert '"level":"INFO"' in output
        assert '"logger":"test.logger"' in output
        assert '"message":"Hello world"' in output
