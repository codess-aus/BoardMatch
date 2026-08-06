"""Resilience helpers for outbound calls to external services.

Provides a small circuit breaker plus tenacity-based retry-with-backoff
configuration, intended to wrap the *actual* HTTP/SDK call sites for Azure
OpenAI, Azure Document Intelligence, and Microsoft Graph — not business
logic. Wrapping close to the network boundary means retries only re-attempt
the external call itself, and the breaker only trips on genuine external
failures.

Usage::

    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

    @with_resilience(breaker, retry_exceptions=(TimeoutError, ConnectionError))
    def call_external_service(...):
        ...
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(StrEnum):
    """Circuit breaker state machine."""

    CLOSED = "closed"  # Normal operation, calls pass through
    OPEN = "open"  # Failing fast, calls are rejected
    HALF_OPEN = "half_open"  # Trial period after recovery_timeout elapses


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, service_name: str, retry_after: float) -> None:
        self.service_name = service_name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker for '{service_name}' is open; "
            f"retry after {retry_after:.1f}s"
        )


@dataclass
class CircuitBreaker:
    """A simple, thread-unsafe (single-process) circuit breaker.

    - Starts CLOSED. Each failure increments a counter; once the counter
      reaches ``failure_threshold`` the breaker trips to OPEN.
    - While OPEN, calls are rejected immediately (fail fast) until
      ``recovery_timeout`` seconds have elapsed, at which point the breaker
      moves to HALF_OPEN and allows a single trial call through.
    - A successful trial call in HALF_OPEN closes the breaker and resets the
      failure counter. A failed trial call re-opens it.
    """

    name: str = "external-service"
    failure_threshold: int = 5
    recovery_timeout: float = 30.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._recovery_elapsed():
            self._state = CircuitState.HALF_OPEN
        return self._state

    def _recovery_elapsed(self) -> bool:
        return (time.monotonic() - self._opened_at) >= self.recovery_timeout

    def before_call(self) -> None:
        """Raise if the circuit is open and not yet eligible for a trial call."""
        if self.state == CircuitState.OPEN:
            retry_after = max(
                0.0, self.recovery_timeout - (time.monotonic() - self._opened_at)
            )
            raise CircuitBreakerOpenError(self.name, retry_after)

    def record_success(self) -> None:
        """Record a successful call, closing the breaker."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call, tripping the breaker if the threshold is hit."""
        self._failure_count += 1
        if (
            self._state == CircuitState.HALF_OPEN
            or self._failure_count >= self.failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit breaker '%s' opened after %d failures",
                self.name,
                self._failure_count,
            )

    def reset(self) -> None:
        """Force the breaker back to CLOSED (used in tests)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Invoke ``func`` guarded by the breaker's state."""
        self.before_call()
        try:
            result = func(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result


def with_resilience(
    breaker: CircuitBreaker | None = None,
    *,
    max_attempts: int = 3,
    min_wait_seconds: float = 0.5,
    max_wait_seconds: float = 8.0,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator combining tenacity retry-with-backoff and an optional circuit breaker.

    Retries transient failures (matching ``retry_exceptions``) up to
    ``max_attempts`` times with exponential backoff and jitter. If a
    ``breaker`` is supplied, the whole retrying call is additionally guarded
    by it: an open breaker fails fast without waiting for the retry loop.
    """

    tenacity_retry = retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=min_wait_seconds, max=max_wait_seconds),
        retry=retry_if_exception_type(retry_exceptions),
    )

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        retrying_func = tenacity_retry(func)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            if breaker is None:
                return retrying_func(*args, **kwargs)
            return breaker.call(retrying_func, *args, **kwargs)

        return wrapper

    return decorator
