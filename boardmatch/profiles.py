"""Candidate profile loading.

In production the CV is parsed with Azure AI Document Intelligence; for the demo
a structured JSON profile is loaded, and a lightweight text extractor is
provided so a pasted CV can be turned into a profile offline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Candidate, Connection

DATA_DIR = Path(__file__).parent / "data"
SAMPLE_PROFILE = DATA_DIR / "sample_candidate.json"

KNOWN_SKILLS = (
    "governance",
    "finance",
    "risk management",
    "cyber security",
    "esg",
    "audit committee",
    "remuneration",
    "digital transformation",
    "regulatory compliance",
    "stakeholder engagement",
    "public policy",
    "people strategy",
    "capital raising",
    "fundraising",
)


def candidate_from_dict(raw: dict) -> Candidate:
    return Candidate(
        name=raw["name"],
        headline=raw.get("headline", ""),
        years_experience=int(raw.get("years_experience", 0)),
        skills=list(raw.get("skills", [])),
        sectors=list(raw.get("sectors", [])),
        credentials=list(raw.get("credentials", [])),
        board_experience=list(raw.get("board_experience", [])),
        achievements=list(raw.get("achievements", [])),
        locations=list(raw.get("locations", [])),
        connections=[
            Connection(
                name=c["name"],
                relationship=c.get("relationship", "Connection"),
                organisations=tuple(c.get("organisations", ())),
                board_seats=tuple(c.get("board_seats", ())),
                strength=float(c.get("strength", 0.5)),
            )
            for c in raw.get("connections", [])
        ],
    )


def load_sample_candidate() -> Candidate:
    with SAMPLE_PROFILE.open(encoding="utf-8") as handle:
        return candidate_from_dict(json.load(handle))


def candidate_from_cv_text(name: str, cv_text: str) -> Candidate:
    """Extract a rough profile from pasted CV text.

    This mirrors the shape of an Azure AI Document Intelligence result so the
    downstream analysis is identical whichever parser produced the profile.
    """
    lowered = cv_text.lower()
    skills = [skill for skill in KNOWN_SKILLS if skill in lowered]

    years = 0
    match = re.search(r"(\d{1,2})\+?\s*years", lowered)
    if match:
        years = int(match.group(1))

    credentials = [
        credential
        for credential, needle in (
            ("AICD Company Directors Course", "company directors course"),
            ("GAICD", "gaicd"),
            ("CA (ANZ)", "chartered accountant"),
            ("MBA", "mba"),
        )
        if needle in lowered
    ]

    achievements = [
        line.strip(" -•\t")
        for line in cv_text.splitlines()
        if line.strip().startswith(("-", "•")) and len(line.strip()) > 12
    ][:5]

    headline = next(
        (line.strip() for line in cv_text.splitlines() if line.strip()), ""
    )

    return Candidate(
        name=name,
        headline=headline[:140],
        years_experience=years,
        skills=skills,
        credentials=credentials,
        achievements=achievements,
    )
