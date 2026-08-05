"""Ingestion package — source adapters and run-tracking models."""

from boardmatch.ingestion.base import (
    OpportunitySource,
    SourceAuthError,
    SourceError,
    SourceRateLimitError,
    SourceTimeoutError,
)
from boardmatch.ingestion.models import IngestionRun, IngestionStatus, SourceRecord

__all__ = [
    "IngestionRun",
    "IngestionStatus",
    "OpportunitySource",
    "SourceAuthError",
    "SourceError",
    "SourceRateLimitError",
    "SourceRecord",
    "SourceTimeoutError",
]
