# Operational Dashboards

This document describes the operational dashboards and monitoring setup for BoardMatch.

## Overview

BoardMatch uses in-process metrics collection with structured JSON logging. Metrics are emitted during normal application operation and are now exposed externally through a `/metrics` endpoint plus a scheduled alert-evaluation loop that routes firing alerts to a webhook.

The current implementation defines metrics and alert rules in `boardmatch/monitoring.py`.

## Metrics export

### `GET /metrics`

The app exposes `GET /metrics` (see `boardmatch/api/metrics.py`), rendering every counter, gauge, and histogram currently held by `MetricsCollector` in [Prometheus text exposition format](https://github.com/prometheus/docs/blob/main/content/docs/instrumenting/exposition_formats.md).

```text
# HELP http_request_duration BoardMatch metric: http_request_duration
# TYPE http_request_duration summary
http_request_duration_count{method="GET",path="/api/v1/opportunities",status_code="200"} 42
http_request_duration_sum{method="GET",path="/api/v1/opportunities",status_code="200"} 918.4
# HELP http_error_count BoardMatch metric: http_error_count
# TYPE http_error_count counter
http_error_count{method="GET",status_code="500"} 1.0
```

Histograms are exported as `_count`/`_sum` series (no fixed buckets), since `MetricsCollector` stores raw observations rather than pre-bucketed counts.

**Why hand-rolled instead of `prometheus-client`?** `MetricsCollector` supports arbitrary, PII-sanitized label sets that can differ per call for the same metric name. `prometheus-client` requires each metric's label *names* to be declared once up front, which doesn't fit that dynamic model without either constraining `MetricsCollector`'s API or maintaining two parallel instrumentation paths. Hand-rolling the exposition text keeps `MetricsCollector` as the single source of truth. If BoardMatch's metrics later stabilize onto a fixed label schema, migrating to `prometheus-client` (or an OpenTelemetry metrics exporter/`azure-monitor-opentelemetry`) is a reasonable follow-up — the `render_prometheus_text()` call site in `boardmatch/api/metrics.py` is the only place that would need to change.

### Wiring into real dashboards

- **Prometheus**: add a scrape config pointing at `/metrics`, e.g.:

  ```yaml
  scrape_configs:
    - job_name: boardmatch
      metrics_path: /metrics
      static_configs:
        - targets: ["boardmatch-host:8000"]
  ```

- **Azure Monitor managed Prometheus (AKS/Container Apps)**: configure the managed Prometheus add-on / scrape config to target the app's `/metrics` endpoint the same way as a self-hosted Prometheus; metrics then flow into Azure Monitor workspaces and can be visualized in Azure Managed Grafana.
- **Grafana**: point a Prometheus datasource at the scraped metrics and build panels using the metric names in the table below.
- **Azure Monitor / Application Insights (alternative approach)**: if deeper distributed tracing is needed later, an OpenTelemetry SDK + `azure-monitor-opentelemetry` exporter could be added alongside (not instead of) `/metrics`, forwarding structured logs and traces to Application Insights. This is left as a follow-up since it requires a `APPLICATIONINSIGHTS_CONNECTION_STRING` to be provisioned.

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
| `retention_cleanup_runs` | Counter | Retention cleanup job invocations | `success` |
| `retention_documents_deleted` | Counter | Documents deleted by retention cleanup | — |
| `retention_texts_deleted` | Counter | Extracted texts deleted by retention cleanup | — |

## PII safety

Metric labels are sanitized before storage. The following label keys are never emitted:

- `email`
- `user_email`
- `phone`
- `ssn`
- `password`
- `token`

Structured log formatting redacts email addresses, phone numbers, and SSNs. Privacy-specific utilities in `boardmatch/retention.py` also redact token-like values. Since `/metrics` renders directly from `MetricsCollector`'s sanitized samples, exported series inherit the same PII protections.

## Alert rules

Alert rules are defined in `boardmatch/monitoring.py` as `ALERT_RULES` and can be evaluated with `evaluate_alerts()`.

### Scheduled evaluation and notification

A background asyncio task, started from the FastAPI `lifespan` (see `boardmatch/api/__init__.py`), calls `evaluate_alerts()` every `ALERT_EVALUATION_INTERVAL_SECONDS` (default 60s) and routes any `FIRING` results to `notify_firing_alerts()`:

- If `ALERT_WEBHOOK_URL` is unset, firing alerts are only logged (WARNING for `warning` severity, ERROR for `critical`) — a safe no-op default for local/dev/test.
- If `ALERT_WEBHOOK_URL` is set, a JSON payload describing all currently-firing alerts is POSTed to it, with retry-with-backoff and a circuit breaker (see `boardmatch/resilience.py`) guarding the outbound call, and an explicit request timeout.

Configure via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `ALERT_WEBHOOK_URL` | empty (log-only) | Webhook endpoint (e.g. a Teams/Slack incoming webhook, or a custom relay) that receives firing-alert JSON payloads |
| `ALERT_EVALUATION_INTERVAL_SECONDS` | `60` | How often the background loop evaluates alert rules |

The webhook payload shape:

```json
{
  "alerts": [
    {
      "name": "database_failure",
      "description": "Database connection or query failures detected",
      "severity": "critical",
      "status": "firing",
      "metric": "database_latency",
      "current_value": 6200.0,
      "threshold": 5000.0,
      "timestamp": 1735689600.0
    }
  ]
}
```

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
2. **Response time P50/P95/P99** — Percentiles of `http_request_duration` (note: with the hand-rolled `/metrics` export, only count/sum are exposed; for true percentile panels, either bucket client-side in Grafana using `histogram_quantile` once real Prometheus buckets are added, or compute average latency from count/sum as an interim signal).
3. **Error rate** — `http_error_count` as a percentage of total requests.
4. **Status code distribution** — Breakdown by `status_code`.

### Ingestion health dashboard

1. **Ingestion success rate** — `ingestion_success_count / (success + failure)` over time.
2. **Failure count** — `ingestion_failure_count` per `source_key`.
3. **Stale opportunities** — Current value of `stale_opportunity_count`.
4. **Last successful run** — Time since the last successful ingestion run.

### Infrastructure dashboard

1. **Database latency** — Count/sum of recorded `database_latency` observations.
2. **Document processing errors** — Rate of `document_processing_failures`.
3. **AI generation errors** — Rate of `ai_generation_failures`.
4. **Graph sync errors** — Rate of `graph_sync_failures`.

### Retention dashboard

1. **Cleanup runs** — `retention_cleanup_runs` by `success` label, to confirm the scheduled job (see `scripts/run_retention_cleanup.py` and `docs/scheduled-jobs.md`) is running.
2. **Documents/texts deleted** — `retention_documents_deleted` and `retention_texts_deleted` trends over time.

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

## Health checks

- **Liveness**: `GET /health/live` — returns `200` when the process is running.
- **Readiness**: `GET /health/ready` — currently returns `200` with `{"status":"ok"}`. It is a placeholder for future dependency checks and does not currently validate database connectivity or emit database latency metrics.

## Follow-ups

- Wire a real `APPLICATIONINSIGHTS_CONNECTION_STRING` if/when Application Insights becomes the primary sink, using an OpenTelemetry exporter alongside the existing `/metrics` endpoint.
- Once `database_latency` is actually recorded by a real database-backed repository layer, connect that alert to real query latency (currently only populated where callers explicitly call `record_database_latency()`).
- Consider adding real Prometheus histogram buckets if percentile dashboards become a priority.

