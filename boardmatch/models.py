"""Core domain models for BoardMatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Remuneration(str, Enum):
    """Whether a board seat is paid, voluntary or undisclosed."""

    PAID = "paid"
    VOLUNTARY = "voluntary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Opportunity:
    """A board / NED vacancy aggregated from a public source."""

    id: str
    title: str
    organisation: str
    sector: str
    location: str
    source: str
    url: str
    remuneration: Remuneration
    fee_aud: Optional[int] = None
    closes_on: Optional[str] = None
    summary: str = ""
    required_skills: tuple[str, ...] = ()
    desirable_skills: tuple[str, ...] = ()

    @property
    def is_paid(self) -> bool:
        return self.remuneration is Remuneration.PAID

    @property
    def fee_display(self) -> str:
        if self.fee_aud:
            return f"AUD ${self.fee_aud:,}/yr"
        return "Not disclosed" if self.is_paid else "Unpaid"


@dataclass
class Candidate:
    """The aspiring director being coached."""

    name: str
    headline: str = ""
    years_experience: int = 0
    skills: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)
    board_experience: list[str] = field(default_factory=list)
    achievements: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    connections: list["Connection"] = field(default_factory=list)

    def normalised_skills(self) -> set[str]:
        return {s.strip().lower() for s in self.skills if s.strip()}


@dataclass(frozen=True)
class Connection:
    """A person in the candidate's network (e.g. from Microsoft Graph)."""

    name: str
    relationship: str
    organisations: tuple[str, ...] = ()
    board_seats: tuple[str, ...] = ()
    strength: float = 0.5  # 0..1, how warm the relationship is


@dataclass(frozen=True)
class FitResult:
    """Fit score plus the named gaps for one opportunity."""

    opportunity: Opportunity
    score: int  # 0..100
    matched_skills: tuple[str, ...]
    missing_required: tuple[str, ...]
    missing_desirable: tuple[str, ...]
    rationale: tuple[str, ...]
    gap_actions: tuple[str, ...]

    @property
    def band(self) -> str:
        if self.score >= 75:
            return "Strong fit"
        if self.score >= 50:
            return "Credible fit"
        return "Stretch"


@dataclass(frozen=True)
class IntroPath:
    """A warm introduction route to an opportunity."""

    opportunity_id: str
    connection: Connection
    reason: str
    warmth: int  # 0..100


class ApplicationStage(str, Enum):
    RESEARCHING = "researching"
    OUTREACH_SENT = "outreach_sent"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFERED = "offered"
    CLOSED = "closed"


@dataclass
class Application:
    """An entry in the candidate's board pipeline."""

    opportunity_id: str
    stage: ApplicationStage = ApplicationStage.RESEARCHING
    notes: str = ""
    id: str = ""


VALID_STAGE_TRANSITIONS: dict[ApplicationStage, set[ApplicationStage]] = {
    ApplicationStage.RESEARCHING: {
        ApplicationStage.OUTREACH_SENT,
        ApplicationStage.APPLIED,
        ApplicationStage.CLOSED,
    },
    ApplicationStage.OUTREACH_SENT: {
        ApplicationStage.APPLIED,
        ApplicationStage.INTERVIEWING,
        ApplicationStage.CLOSED,
    },
    ApplicationStage.APPLIED: {
        ApplicationStage.INTERVIEWING,
        ApplicationStage.OFFERED,
        ApplicationStage.CLOSED,
    },
    ApplicationStage.INTERVIEWING: {
        ApplicationStage.OFFERED,
        ApplicationStage.CLOSED,
    },
    ApplicationStage.OFFERED: {
        ApplicationStage.CLOSED,
    },
    ApplicationStage.CLOSED: set(),
}


@dataclass(frozen=True)
class ApplicationEvent:
    """An immutable record of a stage transition for an application."""

    id: str
    application_id: str
    previous_stage: ApplicationStage
    new_stage: ApplicationStage
    timestamp: datetime
    notes: str = ""



@dataclass
class NetworkConnection:
    """A persisted network connection with approval and strength metadata."""

    id: str
    user_id: str
    name: str
    relationship: str
    organisations: list[str] = field(default_factory=list)
    board_seats: list[str] = field(default_factory=list)
    approved: bool = False
    strength: int = 5  # 1-10, user-adjustable
    source: str = "manual"
    deleted: bool = False



@dataclass(frozen=True)
class FitEvaluation:
    """A persisted fit evaluation with versioning for audit."""

    id: str
    user_id: str
    opportunity_id: str
    profile_version: int
    scoring_version: str
    score: int
    band: str
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    rationale: tuple[str, ...]
    gap_actions: tuple[str, ...]
    created_at: datetime
