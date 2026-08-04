"""Readiness tracker.

Maintains the candidate's board pipeline and computes a board-readiness score
that improves as gaps are closed and applications progress.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Application, ApplicationStage, Candidate, FitResult

STAGE_POINTS: dict[ApplicationStage, int] = {
    ApplicationStage.RESEARCHING: 1,
    ApplicationStage.OUTREACH_SENT: 3,
    ApplicationStage.APPLIED: 5,
    ApplicationStage.INTERVIEWING: 8,
    ApplicationStage.OFFERED: 12,
    ApplicationStage.CLOSED: 0,
}

CORE_GOVERNANCE_SKILLS = ("governance", "finance", "risk management", "esg", "cyber security")


@dataclass
class ReadinessTracker:
    """Tracks applications and derives the board-readiness score."""

    candidate: Candidate
    applications: dict[str, Application] = field(default_factory=dict)

    def track(self, opportunity_id: str, stage: ApplicationStage, notes: str = "") -> Application:
        application = Application(opportunity_id=opportunity_id, stage=stage, notes=notes)
        self.applications[opportunity_id] = application
        return application

    def pipeline(self) -> list[Application]:
        return list(self.applications.values())

    def stage_counts(self) -> dict[str, int]:
        counts = {stage.value: 0 for stage in ApplicationStage}
        for application in self.applications.values():
            counts[application.stage.value] += 1
        return counts

    def credentials_score(self) -> int:
        """0..40 — governance credentials and prior board seats."""
        score = 0
        blob = " ".join(self.candidate.credentials).lower()
        if "company directors course" in blob or "gaicd" in blob:
            score += 20
        elif self.candidate.credentials:
            score += 8
        score += min(20, 10 * len(self.candidate.board_experience))
        return min(40, score)

    def skills_score(self) -> int:
        """0..30 — coverage of the skills boards recruit for."""
        skills = self.candidate.normalised_skills()
        covered = sum(1 for skill in CORE_GOVERNANCE_SKILLS if skill in skills)
        return round(30 * covered / len(CORE_GOVERNANCE_SKILLS))

    def pipeline_score(self) -> int:
        """0..30 — momentum in the pipeline."""
        points = sum(STAGE_POINTS[a.stage] for a in self.applications.values())
        return min(30, points * 2)

    def readiness_score(self) -> int:
        return min(
            100, self.credentials_score() + self.skills_score() + self.pipeline_score()
        )

    def next_actions(self, fits: list[FitResult], limit: int = 5) -> list[str]:
        """The highest-value gap-closing actions across the top opportunities."""
        actions: list[str] = []
        for fit in fits:
            for action in fit.gap_actions:
                if action not in actions:
                    actions.append(action)
        if not self.applications:
            actions.insert(0, "Start your pipeline: track at least three paid seats this week.")
        return actions[:limit]

    def snapshot(self, fits: list[FitResult]) -> dict:
        return {
            "candidate": self.candidate.name,
            "readiness_score": self.readiness_score(),
            "components": {
                "credentials": self.credentials_score(),
                "skills": self.skills_score(),
                "pipeline": self.pipeline_score(),
            },
            "stage_counts": self.stage_counts(),
            "paid_opportunities_in_reach": sum(
                1 for f in fits if f.opportunity.is_paid and f.score >= 50
            ),
            "next_actions": self.next_actions(fits),
        }
