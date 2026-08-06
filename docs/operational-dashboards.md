# Operational Dashboards

This document describes the operational dashboards and monitoring setup for BoardMatch.

## Overview

BoardMatch currently uses in-process metrics collection with structured JSON logging. Metrics are emitted during normal application operation and can be adapted for external monitoring systems such as Prometheus, Azure Monitor, Datadog, or another observability platform.

The current implementation defines metrics and alert rules in `boardmatch/monitoring.py`. It does not yet expose a Prometheus-format `/metrics` endpoint; external scraping/export should be added by adapting the `MetricsCollector` data.

## Metrics

| Metric name | Type | Description | Labels |
|---|---|---|---|
| `http_request_duration` | Histogram | Request processing time in milliseconds | `method`, `path`, `status_code` |
| `http_error_count` | Counter | Count of HTTP error responses | `status_code`, `method` |
| `database_latency` | Histogram | Database operation latency in milliseconds when recorded by services | — |
| `ingestion_success_count` | Counter | Successful ingestion runs | `source_key` |
| `ingestion_failure_count` | Counter | Failed ingestion runs | `source_key` |
| `stale_opportunity_count` | Gauge | Number of opportunities past refresh SLA | — |
| `document_processing_failures` | Counter | Document processing errors | — |
| `ai_generation_failures` | Counter | AI content generation errors | — |
| `graph_sync_failures` | Counter | Graph database sync errors | — |

## PII safety

Metric labels are sanitized before storage. The following label keys are never emitted:

- `email`
- `user_email`
- `phone`
- `ssn`
- `password`
- `token`

Structured log formatting redacts email addresses, phone numbers, and SSNs. Privacy-specific utilities in `boardmatch/retention.py` also redact token-like values.

## Alert rules

Alert rules are defined in `boardmatch/monitoring.py` as `ALERT_RULES` and can be evaluated with `evaluate_alerts()`.

### Critical alerts

| Alert | Condition | Window | Action |
|---|---|---|---|
| **Database Failure** | `database_latency > 5000ms` | 60s | Check database connectivity, connection pool, and recent migration/deployment changes. |
| **Repeated Ingestion Failure** | `ingestion_failure_count >= 2` | 5 min | Investigate source availability, credentials, network access, and parser changes. |

### Warning alerts

| Alert | Condition | Window | Action |
|---|---|---|---|
| **Stale Opportunity Data** | `stale_opportunity_count > 0` | 1 hour | Verify ingestion is running and source records are refreshing. |
| **Auth Failure Spike** | `http_error_count{status_code=401} > 10` | 5 min | Review access logs for invalid credentials or possible credential stuffing. |

## Dashboard panels

### Request performance dashboard

1. **Request rate** — Requests per second grouped by `method` and `path`.
2. **Response time P50/P95/P99** — Percentiles of `http_request_duration`.
3. **Error rate** — `http_error_count` as a percentage of total requests.
4. **Status code distribution** — Breakdown by `status_code`.

### Ingestion health dashboard

1. **Ingestion success rate** — `ingestion_success_count / (success + failure)` over time.
2. **Failure count** — `ingestion_failure_count` per `source_key`.
3. **Stale opportunities** — Current value of `stale_opportunity_count`.
4. **Last successful run** — Time since the last successful ingestion run.

### Infrastructure dashboard

1. **Database latency** — P50/P95/P99 of recorded `database_latency` observations.
2. **Document processing errors** — Rate of `document_processing_failures`.
3. **AI generation errors** — Rate of `ai_generation_failures`.
4. **Graph sync errors** — Rate of `graph_sync_failures`.

## Structured logging

Application logs are formatted as JSON-style records:

```json
{
  "timestamp": "2024-01-15T10:30:00",
  "level": "INFO",
  "logger": "boardmatch.api.middleware",
  "message": "GET /health/live 200 1.2ms request_id=abc-123"
}
```

### Log levels

- **ERROR** — Unhandled exceptions, database failures, external service errors.
- **WARNING** — Degraded performance, approaching limits, non-critical failures.
- **INFO** — Request/response logs, ingestion completions, state changes.
- **DEBUG** — Detailed tracing; do not enable in production.

## Integration guide

### Prometheus

Add a `/metrics` endpoint or sidecar that converts `MetricsCollector` counters, histograms, gauges, and samples into Prometheus exposition format.

### Azure Monitor

Use an OpenTelemetry or Azure Monitor exporter to forward structured logs and metric samples to Application Insights.

### Alerting configuration

`ALERT_RULES` can be:

1. Evaluated in-process and forwarded to alerting systems.
2. Translated into external alerting platform configuration.
3. Used as a source for Prometheus alerting rules once a Prometheus exporter is added.

### Health checks

- **Liveness**: `GET /health/live` — returns `200` when the process is running.
- **Readiness**: `GET /health/ready` — currently returns `200` with `{"status":"ok"}`. It is a placeholder for future dependency checks and does not currently validate database connectivity or emit database latency metrics.
