"""Two-minute command line demo: `python -m boardmatch`."""

from __future__ import annotations

import argparse

from . import coach, discovery, network, profiles
from .fit import rank
from .models import ApplicationStage
from .readiness import ReadinessTracker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boardmatch", description=__doc__)
    parser.add_argument("--paid-only", action="store_true", default=True)
    parser.add_argument("--all", dest="paid_only", action="store_false")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)

    candidate = profiles.load_sample_candidate()
    tracker = ReadinessTracker(candidate=candidate)
    opportunities = discovery.discover(paid_only=args.paid_only)
    fits = rank(candidate, opportunities, limit=args.limit)

    print(f"BoardMatch — {candidate.name}")
    print(f"{len(fits)} {'paid ' if args.paid_only else ''}board seats matched\n")

    for fit in fits:
        opportunity = fit.opportunity
        print(
            f"[{fit.score:>3}] {fit.band:<14} {opportunity.title} — {opportunity.organisation}"
        )
        print(
            f"      {opportunity.fee_display} · {opportunity.location} · {opportunity.source}"
        )
        if fit.gap_actions:
            print(f"      Gap: {fit.gap_actions[0]}")
        path = network.best_path(candidate, opportunity)
        if path:
            print(f"      Warm path: {path.reason}")
        print()

    if fits:
        top = fits[0]
        tracker.track(top.opportunity.id, ApplicationStage.APPLIED)
        print("--- Tailored board CV -------------------------------------------")
        print(coach.board_cv(candidate, top).content)
        print("--- Outreach ----------------------------------------------------")
        path = network.best_path(candidate, top.opportunity)
        print(
            coach.outreach_message(
                candidate,
                top.opportunity,
                intro_via=path.connection.name if path else None,
            ).content
        )

    snapshot = tracker.snapshot(fits)
    print("--- Readiness ---------------------------------------------------")
    print(
        f"Board-readiness score: {snapshot['readiness_score']}/100 {snapshot['components']}"
    )
    for action in snapshot["next_actions"]:
        print(f" - {action}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
