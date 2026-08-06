"""Base protocol and error hierarchy for opportunity source adapters."""

from __future__ import annotations

from typing import Protocol

from boardmatch.models import Opportunity

# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class SourceError(Exception):
    """Base error for source adapter failures."""


class SourceTimeoutError(SourceError):
    """Raised when the upstream source does not respond in time."""


class SourceAuthError(SourceError):
    """Raised when authentication with the upstream source fails."""


class SourceRateLimitError(SourceError):
    """Raised when the upstream source rejects requests due to rate limiting."""


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class OpportunitySource(Protocol):
    """Adapter contract for one approved vacancy source."""

    source_key: str

    def fetch(self) -> list[Opportunity]:
        """Fetch and normalise opportunities from the external source."""
        ...  # pragma: no cover
