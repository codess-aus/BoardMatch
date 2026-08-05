"""Data models for ingestion run tracking and audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class IngestionStatus(Enum):
    """Lifecycle status of an ingestion run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class SourceRecord:
    """Audit record linking an opportunity back to its raw source data."""

    source_key: str
    external_id: str
    raw_data: dict
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class IngestionRun:
    """Tracks metadata for a single ingestion execution."""

    source_key: str
    status: IngestionStatus = IngestionStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    records_fetched: int = 0
    records_stored: int = 0
    records_created: int = 0
    records_updated: int = 0
    records_deactivated: int = 0
    error_message: Optional[str] = None

    def start(self) -> None:
        """Mark the run as started."""
        self.status = IngestionStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)

    def succeed(self, *, fetched: int, stored: int) -> None:
        """Mark the run as successfully completed."""
        self.status = IngestionStatus.SUCCEEDED
        self.completed_at = datetime.now(timezone.utc)
        self.records_fetched = fetched
        self.records_stored = stored

    def fail(self, error: str) -> None:
        """Mark the run as failed."""
        self.status = IngestionStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)
        self.error_message = error
