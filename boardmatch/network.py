"""Network path-finder.

Board seats are relationship-driven, so BoardMatch looks for warm introduction
paths from the candidate's network (sourced from Microsoft Graph in production,
seeded from the sample profile in the demo).
"""

from __future__ import annotations

from .models import Candidate, Connection, IntroPath, Opportunity

DIRECT_SEAT_BONUS = 45
SAME_ORG_BONUS = 30
SAME_SECTOR_BONUS = 15
SEARCH_FIRM_BONUS = 10


def _sits_on(connection: Connection, organisation: str) -> bool:
    return any(seat.lower() == organisation.lower() for seat in connection.board_seats)


def _works_at(connection: Connection, organisation: str) -> bool:
    return any(org.lower() == organisation.lower() for org in connection.organisations)


def paths_for(candidate: Candidate, opportunity: Opportunity) -> list[IntroPath]:
    """Rank warm introduction routes into one board."""
    paths: list[IntroPath] = []
    for connection in candidate.connections:
        warmth = round(connection.strength * 30)
        reasons: list[str] = []

        if _sits_on(connection, opportunity.organisation):
            warmth += DIRECT_SEAT_BONUS
            reasons.append(f"sits on the {opportunity.organisation} board")
        if _works_at(connection, opportunity.organisation):
            warmth += SAME_ORG_BONUS
            reasons.append(f"is connected to {opportunity.organisation}")
        if connection.board_seats and not reasons:
            warmth += SAME_SECTOR_BONUS
            reasons.append(
                "holds a comparable board seat at " + connection.board_seats[0]
            )
        if "search" in connection.relationship.lower():
            warmth += SEARCH_FIRM_BONUS
            reasons.append("can brief the search firm running the process")

        if not reasons:
            continue

        paths.append(
            IntroPath(
                opportunity_id=opportunity.id,
                connection=connection,
                reason=(
                    f"{connection.name} ({connection.relationship.lower()}) "
                    + " and ".join(reasons)
                    + "."
                ),
                warmth=max(0, min(100, warmth)),
            )
        )
    return sorted(paths, key=lambda p: -p.warmth)


def best_path(candidate: Candidate, opportunity: Opportunity) -> IntroPath | None:
    """The single warmest introduction route, if any."""
    paths = paths_for(candidate, opportunity)
    return paths[0] if paths else None
