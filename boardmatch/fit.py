"""Fit and gap analysis.

Scores a candidate against a board's stated needs and names the specific gaps
that would have to be closed. The scoring is deterministic so the demo is
reproducible; an Azure OpenAI reranker can be layered on top of it.
"""

from __future__ import annotations

from .models import Candidate, FitResult, Opportunity

REQUIRED_WEIGHT = 60
DESIRABLE_WEIGHT = 20
SECTOR_WEIGHT = 10
CREDENTIAL_WEIGHT = 10

DIRECTOR_COURSE = "aicd company directors course"

GAP_PLAYBOOK: dict[str, str] = {
    DIRECTOR_COURSE: "Complete the AICD Company Directors Course — it is the default credential screen for Australian boards.",
    "audit committee": "Seek audit or finance sub-committee exposure, even on a not-for-profit board, to evidence financial oversight.",
    "cyber security": "Take a board-level cyber briefing (e.g. AICD/ASD Cyber Governance Principles) and document a cyber incident you have overseen.",
    "esg": "Build an evidenced ESG story — modern slavery, climate reporting or a sustainability steering group role.",
    "risk management": "Show ownership of an enterprise risk framework or a regulatory remediation program.",
    "governance": "Take a first governance seat (NFP or advisory board) to evidence director-level decision making.",
    "remuneration": "Get exposure to executive remuneration setting, ideally via a people and culture committee.",
    "finance": "Demonstrate you can read and challenge statutory accounts — a finance sub-committee seat is the fastest proof.",
    "capital raising": "Document your role in a capital raise or M&A transaction from the board's perspective.",
    "public policy": "Build public-sector credibility through an advisory committee or government consultation panel.",
}


def _gap_action(skill: str, opportunity: Opportunity) -> str:
    key = skill.strip().lower()
    if key in GAP_PLAYBOOK:
        return GAP_PLAYBOOK[key]
    return (
        f"Evidence '{skill}' with a concrete example relevant to {opportunity.organisation} "
        f"({opportunity.sector})."
    )


def _has_director_course(candidate: Candidate) -> bool:
    haystack = " ".join(candidate.credentials + candidate.skills).lower()
    return "company directors course" in haystack or "gaicd" in haystack


def score_opportunity(candidate: Candidate, opportunity: Opportunity) -> FitResult:
    """Score one opportunity and name the gaps."""
    skills = candidate.normalised_skills()
    required = [s.lower() for s in opportunity.required_skills]
    desirable = [s.lower() for s in opportunity.desirable_skills]

    matched_required = [s for s in required if s in skills]
    missing_required = [s for s in required if s not in skills]
    matched_desirable = [s for s in desirable if s in skills]
    missing_desirable = [s for s in desirable if s not in skills]

    score = 0.0
    if required:
        score += REQUIRED_WEIGHT * len(matched_required) / len(required)
    else:
        score += REQUIRED_WEIGHT
    if desirable:
        score += DESIRABLE_WEIGHT * len(matched_desirable) / len(desirable)

    sector_match = opportunity.sector.lower() in {s.lower() for s in candidate.sectors}
    if sector_match:
        score += SECTOR_WEIGHT

    has_course = _has_director_course(candidate)
    if has_course:
        score += CREDENTIAL_WEIGHT
    elif candidate.board_experience:
        score += CREDENTIAL_WEIGHT / 2

    rationale: list[str] = []
    if matched_required:
        rationale.append(
            "Meets stated requirements: " + ", ".join(sorted(matched_required)) + "."
        )
    if matched_desirable:
        rationale.append(
            "Also brings desirable capability: " + ", ".join(sorted(matched_desirable)) + "."
        )
    if sector_match:
        rationale.append(f"Direct {opportunity.sector} sector experience.")
    if candidate.board_experience:
        rationale.append(
            "Existing board exposure: " + "; ".join(candidate.board_experience) + "."
        )
    if not has_course:
        rationale.append(
            "No AICD Company Directors Course on file — expect this to be screened for."
        )
    if opportunity.is_paid:
        rationale.append(f"Paid seat: {opportunity.fee_display}.")
    else:
        rationale.append(
            "Unpaid or undisclosed remuneration — useful for credential building, not income."
        )

    gap_actions = [_gap_action(s, opportunity) for s in missing_required]
    gap_actions += [_gap_action(s, opportunity) for s in missing_desirable]
    if not has_course:
        gap_actions.append(GAP_PLAYBOOK[DIRECTOR_COURSE])

    # Preserve order while de-duplicating the coaching actions.
    deduped = list(dict.fromkeys(gap_actions))

    return FitResult(
        opportunity=opportunity,
        score=max(0, min(100, round(score))),
        matched_skills=tuple(matched_required + matched_desirable),
        missing_required=tuple(missing_required),
        missing_desirable=tuple(missing_desirable),
        rationale=tuple(rationale),
        gap_actions=tuple(deduped),
    )


def rank(
    candidate: Candidate,
    opportunities: list[Opportunity],
    *,
    limit: int | None = None,
) -> list[FitResult]:
    """Score and rank every opportunity for the candidate."""
    results = sorted(
        (score_opportunity(candidate, o) for o in opportunities),
        key=lambda r: (-r.score, -(r.opportunity.fee_aud or 0)),
    )
    return results[:limit] if limit else results
