"""Prometheus-format metrics export endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Response

from boardmatch.monitoring import PROMETHEUS_CONTENT_TYPE, render_prometheus_text

router = APIRouter(tags=["monitoring"])


@router.get("/metrics")
def get_metrics() -> Response:
    """Expose collected metrics in Prometheus text exposition format.

    Scrape this endpoint from Prometheus, Azure Monitor's managed Prometheus,
    or any compatible collector. See docs/operational-dashboards.md for how
    to wire it into dashboards and alerting.
    """
    body = render_prometheus_text()
    return Response(content=body, media_type=PROMETHEUS_CONTENT_TYPE)
