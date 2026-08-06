"""Tests for boardmatch.resilience — circuit breaker and retry-with-backoff."""

from __future__ import annotations

import pytest

from boardmatch.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    with_resilience,
)


class TestCircuitBreaker:
    def test_starts_closed(self):
        breaker = CircuitBreaker(name="test")
        assert breaker.state == CircuitState.CLOSED

    def test_opens_after_threshold_failures(self):
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        for _ in range(3):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    def test_stays_closed_below_threshold(self):
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED

    def test_success_resets_failure_count(self):
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED

    def test_open_breaker_rejects_calls(self):
        breaker = CircuitBreaker(
            name="test", failure_threshold=1, recovery_timeout=60.0
        )
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerOpenError):
            breaker.before_call()

    def test_transitions_to_half_open_after_recovery_timeout(self):
        breaker = CircuitBreaker(
            name="test", failure_threshold=1, recovery_timeout=0.01
        )
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        import time

        time.sleep(0.02)
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_failure_reopens(self):
        breaker = CircuitBreaker(
            name="test", failure_threshold=1, recovery_timeout=0.01
        )
        breaker.record_failure()
        import time

        time.sleep(0.02)
        assert breaker.state == CircuitState.HALF_OPEN
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    def test_call_records_success(self):
        breaker = CircuitBreaker(name="test", failure_threshold=2)
        result = breaker.call(lambda: "ok")
        assert result == "ok"
        assert breaker.state == CircuitState.CLOSED

    def test_call_records_failure_and_reraises(self):
        breaker = CircuitBreaker(name="test", failure_threshold=2)

        def boom():
            raise ValueError("bad")

        with pytest.raises(ValueError):
            breaker.call(boom)
        assert breaker._failure_count == 1

    def test_reset_clears_state(self):
        breaker = CircuitBreaker(name="test", failure_threshold=1)
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        breaker.reset()
        assert breaker.state == CircuitState.CLOSED


class TestWithResilience:
    def test_retries_transient_failures_then_succeeds(self):
        attempts = {"count": 0}

        @with_resilience(
            None,
            max_attempts=3,
            min_wait_seconds=0.01,
            max_wait_seconds=0.02,
            retry_exceptions=(ConnectionError,),
        )
        def flaky():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ConnectionError("transient")
            return "success"

        assert flaky() == "success"
        assert attempts["count"] == 3

    def test_gives_up_after_max_attempts(self):
        attempts = {"count": 0}

        @with_resilience(
            None,
            max_attempts=3,
            min_wait_seconds=0.01,
            max_wait_seconds=0.02,
            retry_exceptions=(ConnectionError,),
        )
        def always_fails():
            attempts["count"] += 1
            raise ConnectionError("persistent")

        with pytest.raises(ConnectionError):
            always_fails()
        assert attempts["count"] == 3

    def test_does_not_retry_non_matching_exceptions(self):
        attempts = {"count": 0}

        @with_resilience(
            None,
            max_attempts=3,
            min_wait_seconds=0.01,
            max_wait_seconds=0.02,
            retry_exceptions=(ConnectionError,),
        )
        def wrong_error():
            attempts["count"] += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            wrong_error()
        assert attempts["count"] == 1

    def test_breaker_opens_and_prevents_further_calls(self):
        breaker = CircuitBreaker(
            name="test-flaky", failure_threshold=2, recovery_timeout=60.0
        )
        attempts = {"count": 0}

        @with_resilience(
            breaker,
            max_attempts=1,
            min_wait_seconds=0.01,
            max_wait_seconds=0.02,
            retry_exceptions=(ConnectionError,),
        )
        def always_fails():
            attempts["count"] += 1
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            always_fails()
        with pytest.raises(ConnectionError):
            always_fails()

        # Breaker should now be open and reject without invoking the function.
        with pytest.raises(CircuitBreakerOpenError):
            always_fails()
        assert attempts["count"] == 2

    def test_breaker_recovers_after_timeout(self):
        breaker = CircuitBreaker(
            name="test-recover", failure_threshold=1, recovery_timeout=0.01
        )

        @with_resilience(
            breaker,
            max_attempts=1,
            min_wait_seconds=0.01,
            max_wait_seconds=0.02,
            retry_exceptions=(ConnectionError,),
        )
        def sometimes_fails(should_fail: bool):
            if should_fail:
                raise ConnectionError("down")
            return "recovered"

        with pytest.raises(ConnectionError):
            sometimes_fails(True)

        import time

        time.sleep(0.02)
        assert sometimes_fails(False) == "recovered"
        assert breaker.state == CircuitState.CLOSED
