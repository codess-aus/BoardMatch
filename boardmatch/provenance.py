"""Provenance and trust indicators for board opportunities.

Computes trust signals (staleness, confidence, status) from opportunity
metadata and ingestion history to help candidates assess listing quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum

from boardmatch.models import Opportunity, Remuneration

# A record is considered stale if not verified within this many days
STALE_THRESHOLD_DAYS = 14


class OpportunityStatus(str, Enum):
    """Trust-aware status of an opportunity listing."""

    ACTIVE = "active"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    UNVERIFIED = "unverified"


class RemunerationConfidence(str, Enum):
    """Confidence level for remuneration information."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProvenanceInfo:
    """Provenance and trust metadata for an opportunity."""

    source_name: str
    source_url: str | None
    first_seen: datetime | None
    last_verified: datetime | None
    closing_date: date | None
    status: OpportunityStatus
    remuneration_confidence: RemunerationConfidence
    is_stale: bool = False
    duplicate_sources: list[str] | None = None

    @property
    def stale_warning(self) -> str | None:
        if self.is_stale:
            return "This listing has not been verified recently and may be outdated."
        return None


def compute_status(
    opportunity: Opportunity,
    *,
    last_verified: datetime | None = None,
    withdrawn: bool = False,
    now: date | None = None,
) -> OpportunityStatus:
    """Determine the trust-aware status of an opportunity."""
    if withdrawn:
        return OpportunityStatus.WITHDRAWN

    if last_verified is None:
        return OpportunityStatus.UNVERIFIED

    effective_now = now or datetime.now(timezone.utc).date()

    if opportunity.closes_on:
        try:
            closing = date.fromisoformat(opportunity.closes_on)
            if closing < effective_now:
                return OpportunityStatus.EXPIRED
        except (ValueError, TypeError):
            pass

    return OpportunityStatus.ACTIVE


def compute_remuneration_confidence(opportunity: Opportunity) -> RemunerationConfidence:
    """Determine confidence in the remuneration information."""
    if opportunity.remuneration == Remuneration.UNKNOWN:
        return RemunerationConfidence.UNKNOWN
    if opportunity.remuneration == Remuneration.PAID:
        if opportunity.fee_aud is not None and opportunity.fee_aud > 0:
            return RemunerationConfidence.HIGH
        return RemunerationConfidence.MEDIUM
    # Voluntary with explicit declaration
    return RemunerationConfidence.HIGH


def is_stale(
    last_verified: datetime | None,
    *,
    now: datetime | None = None,
    threshold_days: int = STALE_THRESHOLD_DAYS,
) -> bool:
    """Check if a record is stale based on last verification time."""
    if last_verified is None:
        return True
    effective_now = now or datetime.now(timezone.utc)
    delta = effective_now - last_verified
    return delta.days > threshold_days


def build_provenance(
    opportunity: Opportunity,
    *,
    first_seen: datetime | None = None,
    last_verified: datetime | None = None,
    withdrawn: bool = False,
    duplicate_sources: list[str] | None = None,
    now: datetime | None = None,
) -> ProvenanceInfo:
    """Build complete provenance info for an opportunity."""
    effective_now = now or datetime.now(timezone.utc)
    effective_now_date = (
        effective_now.date() if isinstance(effective_now, datetime) else effective_now
    )

    status = compute_status(
        opportunity,
        last_verified=last_verified,
        withdrawn=withdrawn,
        now=effective_now_date,
    )

    remuneration_conf = compute_remuneration_confidence(opportunity)
    stale = is_stale(last_verified, now=effective_now)

    closing_date: date | None = None
    if opportunity.closes_on:
        try:
            closing_date = date.fromisoformat(opportunity.closes_on)
        except (ValueError, TypeError):
            pass

    # Extract source URL from opportunity
    source_url = opportunity.url if opportunity.url else None

    return ProvenanceInfo(
        source_name=opportunity.source,
        source_url=source_url,
        first_seen=first_seen,
        last_verified=last_verified,
        closing_date=closing_date,
        status=status,
        remuneration_confidence=remuneration_conf,
        is_stale=stale,
        duplicate_sources=duplicate_sources,
    )
