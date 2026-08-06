"""Async load-testing script for BoardMatch's key API endpoints.

Exercises, concurrently, against a *running* BoardMatch instance:

- ``GET  /api/v1/opportunities``       (list/filter — read-heavy path)
- ``POST /api/v1/fit-evaluations``     (fit scoring — read+write path)
- ``POST /api/v1/coaching/board-cv``   (coaching draft generation — the
  most expensive/rate-limited path)

Each simulated virtual user gets a unique ``X-Dev-User-Id`` header (dev/local
auth) so requests spread across the in-process rate limiters the same way
distinct real users would, and so fit-evaluation idempotency (keyed on
user + opportunity + profile/scoring version) doesn't collapse all virtual
users onto a single cached evaluation.

This script talks to the app over plain HTTP, the same way a real client or
load balancer would — it does not import the FastAPI app in-process. That
means it equally works against a local ``uvicorn``/``gunicorn`` instance or
a real deployed environment (see ``loadtest/README.md``).

Usage:
    python loadtest/run_load_test.py --base-url http://127.0.0.1:8000 \\
        --concurrency 20 --iterations 25

Requires only ``httpx`` (already a project dependency, see requirements-dev.txt).

Note: in local/SQLite mode, ``POST /api/v1/fit-evaluations`` requires the
target opportunity and the caller's candidate profile to exist in the
fit-evaluations router's own in-memory repositories (a local-mode-only
artifact of per-router repository isolation -- see loadtest/README.md for
details and how to seed them for a representative run). This does not apply
against a real Postgres-backed deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass, field

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEV_USER_HEADER = "X-Dev-User-Id"

# Relative traffic mix. Coaching draft generation is intentionally the
# smallest share: it is both the most expensive endpoint (LLM/template
# generation + validation) and the most tightly rate-limited (10/hour/user
# route-specific limit, plus a shared 30/60s sensitive-path limit covering
# all of /api/v1/coaching). This mirrors realistic usage where a candidate
# browses/evaluates far more often than they regenerate coaching drafts.
DEFAULT_WEIGHTS = {
    "list_opportunities": 0.6,
    "fit_evaluation": 0.3,
    "coaching_board_cv": 0.1,
}


@dataclass
class RequestResult:
    endpoint: str
    status_code: int
    latency_ms: float
    error: str | None = None


@dataclass
class Stats:
    results: list[RequestResult] = field(default_factory=list)

    def add(self, result: RequestResult) -> None:
        self.results.append(result)

    def by_endpoint(self) -> dict[str, list[RequestResult]]:
        grouped: dict[str, list[RequestResult]] = {}
        for r in self.results:
            grouped.setdefault(r.endpoint, []).append(r)
        return grouped

    def print_report(self, wall_time_s: float) -> None:
        total = len(self.results)
        if total == 0:
            print("No requests were completed.")
            return

        errors = [r for r in self.results if r.error is not None]
        server_errors = [r for r in self.results if r.status_code >= 500]
        rate_limited = [r for r in self.results if r.status_code == 429]

        print("\n=== BoardMatch load test summary ===")
        print(f"Wall time:        {wall_time_s:.2f}s")
        print(f"Total requests:   {total}")
        print(f"Throughput:       {total / wall_time_s:.2f} req/s")
        print(f"Transport errors: {len(errors)}")
        print(f"5xx responses:    {len(server_errors)}")
        print(f"429 (rate-limit): {len(rate_limited)}")

        print("\nPer-endpoint breakdown:")
        header = (
            f"{'endpoint':<22}{'count':>8}{'2xx/3xx':>10}"
            f"{'4xx':>7}{'5xx':>7}{'p50 ms':>10}{'p95 ms':>10}{'p99 ms':>10}"
        )
        print(header)
        print("-" * len(header))
        for endpoint, items in sorted(self.by_endpoint().items()):
            latencies = sorted(r.latency_ms for r in items if r.error is None)
            ok = sum(1 for r in items if r.error is None and r.status_code < 400)
            client_err = sum(1 for r in items if 400 <= r.status_code < 500)
            server_err = sum(1 for r in items if r.status_code >= 500)
            p50 = _percentile(latencies, 0.50)
            p95 = _percentile(latencies, 0.95)
            p99 = _percentile(latencies, 0.99)
            print(
                f"{endpoint:<22}{len(items):>8}{ok:>10}{client_err:>7}"
                f"{server_err:>7}{p50:>10.1f}{p95:>10.1f}{p99:>10.1f}"
            )

        if server_errors:
            print(
                "\nWARNING: server (5xx) errors observed — see the raw "
                "results JSON for details."
            )

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "endpoint": r.endpoint,
                    "status_code": r.status_code,
                    "latency_ms": round(r.latency_ms, 2),
                    "error": r.error,
                }
                for r in self.results
            ]
        )


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


async def _timed_request(
    client: httpx.AsyncClient, endpoint: str, method: str, url: str, **kwargs
) -> tuple[RequestResult, httpx.Response | None]:
    start = time.perf_counter()
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(endpoint, 0, latency_ms, error=str(exc)), None
    latency_ms = (time.perf_counter() - start) * 1000
    return RequestResult(endpoint, response.status_code, latency_ms), response


async def _create_profile(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    """Create a minimal candidate profile so fit-evaluations succeed.

    Not itself part of the measured endpoint mix — this is setup, mirroring
    an onboarded user.
    """
    payload = {
        "name": f"Load Test User {headers[DEV_USER_HEADER]}",
        "headline": "Non-Executive Director candidate",
        "years_experience": 12,
        "skills": ["Governance", "Risk", "Finance"],
        "sectors": ["Financial Services"],
        "credentials": ["GAICD"],
        "board_experience": ["Audit Committee Member"],
    }
    await client.request(
        "PUT", "/api/v1/profile", headers=headers, json=payload, timeout=30
    )


async def _list_opportunities(
    client: httpx.AsyncClient, headers: dict[str, str], stats: Stats
) -> str | None:
    result, response = await _timed_request(
        client,
        "list_opportunities",
        "GET",
        "/api/v1/opportunities",
        headers=headers,
        params={"page": 1, "page_size": 20},
        timeout=30,
    )
    stats.add(result)
    if response is not None and response.status_code == 200:
        items = response.json().get("items", [])
        if items:
            return items[0]["id"]
    return None


async def _fit_evaluation(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    stats: Stats,
    opportunity_id: str | None,
) -> None:
    if opportunity_id is None:
        return
    result, _ = await _timed_request(
        client,
        "fit_evaluation",
        "POST",
        "/api/v1/fit-evaluations",
        headers=headers,
        json={"opportunity_id": opportunity_id},
        timeout=30,
    )
    stats.add(result)


async def _coaching_board_cv(
    client: httpx.AsyncClient, headers: dict[str, str], stats: Stats
) -> None:
    result, _ = await _timed_request(
        client,
        "coaching_board_cv",
        "POST",
        "/api/v1/coaching/board-cv",
        headers=headers,
        timeout=30,
    )
    stats.add(result)


async def _virtual_user(
    client: httpx.AsyncClient,
    stats: Stats,
    iterations: int,
    weights: dict[str, float],
) -> None:
    user_id = f"loadtest-{uuid.uuid4().hex[:12]}"
    headers = {DEV_USER_HEADER: user_id}

    await _create_profile(client, headers)
    opportunity_id = await _list_opportunities(client, headers, stats)

    choices = list(weights.keys())
    probs = list(weights.values())

    for _ in range(iterations):
        action = random.choices(choices, weights=probs, k=1)[0]
        if action == "list_opportunities":
            fetched = await _list_opportunities(client, headers, stats)
            opportunity_id = fetched or opportunity_id
        elif action == "fit_evaluation":
            await _fit_evaluation(client, headers, stats, opportunity_id)
        elif action == "coaching_board_cv":
            await _coaching_board_cv(client, headers, stats)


async def run(
    base_url: str,
    concurrency: int,
    iterations: int,
    weights: dict[str, float],
) -> Stats:
    stats = Stats()
    limits = httpx.Limits(
        max_connections=concurrency + 10, max_keepalive_connections=concurrency
    )
    async with httpx.AsyncClient(base_url=base_url, limits=limits) as client:
        start = time.perf_counter()
        await asyncio.gather(
            *(
                _virtual_user(client, stats, iterations, weights)
                for _ in range(concurrency)
            )
        )
        wall_time_s = time.perf_counter() - start
    stats.print_report(wall_time_s)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Number of simulated concurrent virtual users",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=25,
        help="Requests per virtual user (after the initial setup request)",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write raw per-request results as JSON",
    )
    args = parser.parse_args(argv)

    stats = asyncio.run(
        run(args.base_url, args.concurrency, args.iterations, DEFAULT_WEIGHTS)
    )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(stats.to_json())
        print(f"\nRaw results written to {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
