"""Monitoring, metrics collection, and alert rule definitions.

Provides in-process metrics counters and gauges for observability, structured
logging configuration, PII-safe dimension handling, and alert rule definitions
that can be evaluated against collected metrics.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
]


def redact_pii(message: str) -> str:
    """Remove PII patterns from a log message."""
    for pattern, replacement in _PII_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


class StructuredFormatter(logging.Formatter):
    """JSON-style structured log formatter with PII redaction."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        message = redact_pii(message)
        timestamp = self.formatTime(record, self.datefmt)
        return (
            '{"timestamp":"' + timestamp + '",'
            '"level":"' + record.levelname + '",'
            '"logger":"' + record.name + '",'
            '"message":"' + self._escape(message) + '"}'
        )

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def configure_structured_logging(level: str = "INFO") -> None:
    """Configure root logger with structured JSON formatting."""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------


class MetricType(StrEnum):
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    GAUGE = "gauge"


@dataclass
class MetricSample:
    """A single metric observation."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """In-process metrics collector with PII-safe dimensions.

    Collects counters, histograms, and gauges. Labels are validated to
    prevent accidental PII leakage through metric dimensions.
    """

    _BLOCKED_LABELS = frozenset({"email", "user_email", "phone", "ssn", "password", "token"})

    _ALLOWED_LABELS = frozenset({
        "method", "path", "status_code", "source_key", "error_type",
        "endpoint", "service", "environment", "version",
    })

    def __init__(self) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._gauges: dict[str, float] = {}
        self._samples: list[MetricSample] = []

    def _sanitize_labels(self, labels: dict[str, str] | None) -> dict[str, str]:
        """Filter labels to only allow safe dimensions."""
        if not labels:
            return {}
        sanitized = {}
        for key, value in labels.items():
            if key.lower() in self._BLOCKED_LABELS:
                continue
            sanitized[key] = value
        return sanitized

    def _label_key(self, name: str, labels: dict[str, str]) -> str:
        """Create a unique key from metric name and sorted labels."""
        parts = [name] + [f"{k}={v}" for k, v in sorted(labels.items())]
        return "|".join(parts)

    def increment(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment a counter metric."""
        safe_labels = self._sanitize_labels(labels)
        key = self._label_key(name, safe_labels)
        self._counters[key] += value
        self._samples.append(MetricSample(name=name, value=value, labels=safe_labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record a histogram observation (e.g., request duration)."""
        safe_labels = self._sanitize_labels(labels)
        key = self._label_key(name, safe_labels)
        self._histograms[key].append(value)
        self._samples.append(MetricSample(name=name, value=value, labels=safe_labels))

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge to a specific value."""
        safe_labels = self._sanitize_labels(labels)
        key = self._label_key(name, safe_labels)
        self._gauges[key] = value
        self._samples.append(MetricSample(name=name, value=value, labels=safe_labels))

    def get_counter(self, name: str, labels: dict[str, str] | None = None) -> float:
        """Retrieve current counter value."""
        safe_labels = self._sanitize_labels(labels)
        key = self._label_key(name, safe_labels)
        return self._counters.get(key, 0.0)

    def get_histogram(self, name: str, labels: dict[str, str] | None = None) -> list[float]:
        """Retrieve histogram observations."""
        safe_labels = self._sanitize_labels(labels)
        key = self._label_key(name, safe_labels)
        return list(self._histograms.get(key, []))

    def get_gauge(self, name: str, labels: dict[str, str] | None = None) -> float:
        """Retrieve current gauge value."""
        safe_labels = self._sanitize_labels(labels)
        key = self._label_key(name, safe_labels)
        return self._gauges.get(key, 0.0)

    def get_samples(self, name: str | None = None) -> list[MetricSample]:
        """Retrieve collected samples, optionally filtered by name."""
        if name is None:
            return list(self._samples)
        return [s for s in self._samples if s.name == name]

    def reset(self) -> None:
        """Clear all collected metrics."""
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()
        self._samples.clear()


# Module-level default collector instance
metrics = MetricsCollector()


# ---------------------------------------------------------------------------
# Metric names (constants)
# ---------------------------------------------------------------------------

HTTP_REQUEST_DURATION = "http_request_duration"
HTTP_ERROR_COUNT = "http_error_count"
DATABASE_LATENCY = "database_latency"
INGESTION_SUCCESS_COUNT = "ingestion_success_count"
INGESTION_FAILURE_COUNT = "ingestion_failure_count"
STALE_OPPORTUNITY_COUNT = "stale_opportunity_count"
DOCUMENT_PROCESSING_FAILURES = "document_processing_failures"
AI_GENERATION_FAILURES = "ai_generation_failures"
GRAPH_SYNC_FAILURES = "graph_sync_failures"


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------


class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OK = "ok"
    FIRING = "firing"


@dataclass
class AlertRule:
    """Definition of an operational alert rule."""

    name: str
    description: str
    severity: AlertSeverity
    metric_name: str
    threshold: float
    comparison: str = "gt"  # gt, gte, lt, lte, eq
    window_seconds: float = 300.0  # 5 minute default window
    labels: dict[str, str] = field(default_factory=dict)

    def evaluate(self, collector: MetricsCollector) -> AlertStatus:
        """Evaluate this alert rule against collected metrics."""
        value = collector.get_counter(self.metric_name, self.labels)
        if self.comparison == "gt" and value > self.threshold:
            return AlertStatus.FIRING
        if self.comparison == "gte" and value >= self.threshold:
            return AlertStatus.FIRING
        if self.comparison == "lt" and value < self.threshold:
            return AlertStatus.FIRING
        if self.comparison == "lte" and value <= self.threshold:
            return AlertStatus.FIRING
        if self.comparison == "eq" and value == self.threshold:
            return AlertStatus.FIRING
        return AlertStatus.OK


@dataclass
class AlertEvaluation:
    """Result of evaluating an alert rule."""

    rule: AlertRule
    status: AlertStatus
    current_value: float
    timestamp: float = field(default_factory=time.time)


# Pre-defined alert rules
ALERT_RULES: list[AlertRule] = [
    AlertRule(
        name="database_failure",
        description="Database connection or query failures detected",
        severity=AlertSeverity.CRITICAL,
        metric_name=DATABASE_LATENCY,
        threshold=5000.0,
        comparison="gt",
        window_seconds=60.0,
    ),
    AlertRule(
        name="ingestion_failure_repeated",
        description="Multiple ingestion failures within the alert window",
        severity=AlertSeverity.CRITICAL,
        metric_name=INGESTION_FAILURE_COUNT,
        threshold=2.0,
        comparison="gte",
        window_seconds=300.0,
    ),
    AlertRule(
        name="stale_opportunity_data",
        description="Opportunity data has not been refreshed within expected interval",
        severity=AlertSeverity.WARNING,
        metric_name=STALE_OPPORTUNITY_COUNT,
        threshold=0.0,
        comparison="gt",
        window_seconds=3600.0,
    ),
    AlertRule(
        name="auth_failure_spike",
        description="Elevated rate of authentication failures may indicate attack",
        severity=AlertSeverity.WARNING,
        metric_name=HTTP_ERROR_COUNT,
        threshold=10.0,
        comparison="gt",
        window_seconds=300.0,
        labels={"status_code": "401"},
    ),
]


def evaluate_alerts(collector: MetricsCollector | None = None) -> list[AlertEvaluation]:
    """Evaluate all defined alert rules and return their current status."""
    collector = collector or metrics
    results = []
    for rule in ALERT_RULES:
        status = rule.evaluate(collector)
        current_value = collector.get_counter(rule.metric_name, rule.labels)
        results.append(AlertEvaluation(rule=rule, status=status, current_value=current_value))
    return results


# ---------------------------------------------------------------------------
# Middleware integration helpers
# ---------------------------------------------------------------------------


def record_request_duration(method: str, path: str, status_code: int, duration_ms: float) -> None:
    """Record HTTP request duration metric with safe labels."""
    labels = {"method": method, "path": path, "status_code": str(status_code)}
    metrics.observe(HTTP_REQUEST_DURATION, duration_ms, labels)

    if status_code >= 400:
        metrics.increment(HTTP_ERROR_COUNT, labels={"status_code": str(status_code), "method": method})


def record_database_latency(duration_ms: float) -> None:
    """Record database operation latency."""
    metrics.observe(DATABASE_LATENCY, duration_ms)


def record_ingestion_result(source_key: str, success: bool) -> None:
    """Record ingestion run outcome."""
    if success:
        metrics.increment(INGESTION_SUCCESS_COUNT, labels={"source_key": source_key})
    else:
        metrics.increment(INGESTION_FAILURE_COUNT, labels={"source_key": source_key})


def record_stale_opportunities(count: int) -> None:
    """Record the number of stale opportunities detected."""
    metrics.set_gauge(STALE_OPPORTUNITY_COUNT, float(count))
