"""In-memory sliding-window rate limiter for draft generation.

Limits each user to a configurable number of drafts per time window.
Default: 10 drafts per hour.
"""

from __future__ import annotations

import time
from collections import defaultdict

DEFAULT_MAX_REQUESTS = 10
DEFAULT_WINDOW_SECONDS = 3600  # 1 hour


class RateLimiter:
    """Sliding-window rate limiter using in-memory timestamps."""

    def __init__(
        self,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: str) -> bool:
        """Check if the user is within their rate limit."""
        now = time.time()
        cutoff = now - self.window_seconds
        self._requests[user_id] = [
            ts for ts in self._requests[user_id] if ts > cutoff
        ]
        return len(self._requests[user_id]) < self.max_requests

    def record(self, user_id: str) -> None:
        """Record a new request for the user."""
        self._requests[user_id].append(time.time())

    def remaining(self, user_id: str) -> int:
        """Return how many requests the user has left in the current window."""
        now = time.time()
        cutoff = now - self.window_seconds
        self._requests[user_id] = [
            ts for ts in self._requests[user_id] if ts > cutoff
        ]
        return max(0, self.max_requests - len(self._requests[user_id]))

    def reset(self, user_id: str) -> None:
        """Clear rate limit state for a user (useful for testing)."""
        self._requests.pop(user_id, None)


# Module-level singleton for use across coaching endpoints
draft_rate_limiter = RateLimiter()
