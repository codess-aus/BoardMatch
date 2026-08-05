# Operational Dashboards

This document describes the operational dashboards and monitoring setup for BoardMatch.

## Overview

BoardMatch uses in-process metrics collection with structured JSON logging. Metrics are emitted during normal application operation and can be scraped by external monitoring systems (Prometheus, Azure Monitor, Datadog, etc.).

## Metrics

| Metric Name | Type | Description | Labels |
|---|---|---|---|
| `http_request_duration` | Histogram | Request processing time in milliseconds | `method`, `path`, `status_code` |
| `http_error_count` | Counter | Count of HTTP error responses (4xx/5xx) | `status_code`, `method` |
| `database_latency` | Histogram | Database operation latency in milliseconds | — |
| `ingestion_success_count` | Counter | Successful ingestion runs | `source_key` |
| `ingestion_failure_count` | Counter | Failed ingestion runs | `source_key` |
| `stale_opportunity_count` | Gauge | Number of opportunities past refresh SLA | — |
| `document_processing_failures` | Counter | Document processing errors | — |
| `ai_generation_failures` | Counter | AI content generation errors | — |
| `graph_sync_failures` | Counter | Graph database sync errors | — |

### PII Safety

All metric labels are validated against a blocklist. The following label keys are **never** emitted:
- `email`, `user_email`, `phone`, `ssn`, `password`, `token`

Log messages are automatically redacted for email addresses, phone numbers, and SSNs.

## Alert Rules

### Critical Alerts

| Alert | Condition | Window | Action |
|---|---|---|---|
| **Database Failure** | `database_latency > 5000ms` | 60s | Page on-call engineer. Check database connectivity and connection pool. |
| **Repeated Ingestion Failure** | `ingestion_failure_count >= 2` | 5 min | Investigate source availability. Check network connectivity to data sources. |

### Warning Alerts

| Alert | Condition | Window | Action |
|---|---|---|---|
| **Stale Opportunity Data** | `stale_opportunity_count > 0` | 1 hour | Verify ingestion scheduler is running. Check source API status. |
| **Auth Failure Spike** | `http_error_count{status_code=401} > 10` | 5 min | Review access logs for potential credential stuffing. Consider rate limiting. |

## Dashboard Panels

### Request Performance Dashboard

1. **Request Rate** — Requests per second grouped by `method` and `path`
2. **Response Time P50/P95/P99** — Percentiles of `http_request_duration`
3. **Error Rate** — `http_error_count` as a percentage of total requests
4. **Status Code Distribution** — Breakdown by `status_code`

### Ingestion Health Dashboard

1. **Ingestion Success Rate** — `ingestion_success_count / (success + failure)` over time
2. **Failure Count** — `ingestion_failure_count` per `source_key`
3. **Stale Opportunities** — Current value of `stale_opportunity_count`
4. **Last Successful Run** — Time since last `ingestion_success_count` increment

### Infrastructure Dashboard

1. **Database Latency** — P50/P95/P99 of `database_latency`
2. **Document Processing Errors** — `document_processing_failures` rate
3. **AI Generation Errors** — `ai_generation_failures` rate
4. **Graph Sync Errors** — `graph_sync_failures` rate

## Structured Logging

All application logs are emitted in JSON format:

```json
{
  "timestamp": "2024-01-15T10:30:00",
  "level": "INFO",
  "logger": "boardmatch.api.middleware",
  "message": "GET /health/live 200 1.2ms request_id=abc-123"
}
```

### Log Levels

- **ERROR** — Unhandled exceptions, database failures, external service errors
- **WARNING** — Degraded performance, approaching limits, non-critical failures
- **INFO** — Request/response logs, ingestion completions, state changes
- **DEBUG** — Detailed tracing (never enabled in production)

## Integration Guide

### Prometheus

Expose metrics at `/metrics` endpoint using a Prometheus client library. The `MetricsCollector` data can be adapted to Prometheus exposition format.

### Azure Monitor

Use the Azure Monitor OpenTelemetry exporter to forward metrics and structured logs to Application Insights.

### Alerting Configuration

Alert rules are defined in `boardmatch/monitoring.py` as `ALERT_RULES`. These can be:
1. Evaluated in-process and forwarded to alerting systems
2. Translated to external alerting platform configurations (PagerDuty, OpsGenie)
3. Used as Prometheus alerting rules via `evaluate_alerts()`

### Health Checks

- **Liveness**: `GET /health/live` — Process is running
- **Readiness**: `GET /health/ready` — Dependencies are available

Readiness probe integrates with monitoring: failures increment `database_latency` with a high value indicating unreachability.
