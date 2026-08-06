"""Request ID, logging, security, CORS, and rate-limiting middleware."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from boardmatch.config import AppEnvironment, Settings, get_settings
from boardmatch.monitoring import record_request_duration

from .v1.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

# Route prefixes considered sensitive enough to warrant rate limiting beyond
# any per-route limiter (auth, AI generation, document upload).
RATE_LIMITED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/coaching",
    "/api/v1/profile/documents",
    "/api/v1/auth",
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generates a unique request ID per request and attaches it to the response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs method, route, status, duration, and request_id for each request.

    Also emits http_request_duration and http_error_count metrics.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        request_id = getattr(request.state, "request_id", "-")
        logger.info(
            "%s %s %d %.1fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        # Emit metrics for request duration and errors
        record_request_duration(
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds standard security response headers to every response.

    - X-Content-Type-Options: prevents MIME-sniffing.
    - X-Frame-Options: prevents clickjacking via framing.
    - Referrer-Policy: limits referrer leakage to third parties.
    - Strict-Transport-Security: only added in production, since it is only
      meaningful (and safe) when the app is always served over HTTPS.
    """

    def __init__(self, app: FastAPI, *, is_production: bool) -> None:
        super().__init__(app)
        self._is_production = is_production

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if self._is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies an in-process sliding-window rate limit to sensitive routes.

    Sensitive routes are identified by path prefix (auth, coaching/AI
    generation, document upload). Requests are keyed by the authenticated
    dev-user header when present, otherwise by client IP, so that abuse from
    a single caller cannot exhaust another caller's quota.
    """

    def __init__(
        self,
        app: FastAPI,
        *,
        limiter: RateLimiter,
        path_prefixes: tuple[str, ...] = RATE_LIMITED_PATH_PREFIXES,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._path_prefixes = path_prefixes

    def _is_limited_path(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._path_prefixes)

    def _client_key(self, request: Request) -> str:
        user_id = request.headers.get("X-Dev-User-Id")
        if user_id:
            return f"user:{user_id}"
        client = request.client
        return f"ip:{client.host}" if client else "ip:unknown"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not self._is_limited_path(request.url.path):
            return await call_next(request)

        key = self._client_key(request)
        if not self._limiter.is_allowed(key):
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
            )
        self._limiter.record(key)
        return await call_next(request)


def register_middleware(app: FastAPI, settings: Settings | None = None) -> None:
    """Attach middleware to the app. Order matters: first added = outermost."""
    settings = settings or get_settings()
    is_production = settings.app_env == AppEnvironment.PRODUCTION

    # Logging wraps everything (outermost), RequestID inside it so ID is available.
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, is_production=is_production)
    app.add_middleware(
        RateLimitMiddleware,
        limiter=RateLimiter(
            max_requests=settings.rate_limit_max_requests,
            window_seconds=settings.rate_limit_window_seconds,
        ),
    )
    # CORS: restrictive by default (no origins allowed) unless configured.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
