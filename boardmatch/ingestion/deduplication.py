"""Deduplication and canonicalisation for board opportunities.

Normalises organisation names and titles to detect duplicate opportunity
records ingested from different sources. Groups duplicates together and
supports merging into a single canonical record while retaining provenance.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from boardmatch.models import Opportunity

# Common abbreviation mappings (applied after lowercasing)
_ORG_ABBREVIATIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bltd\b\.?"), "limited"),
    (re.compile(r"\bcorp\b\.?"), "corporation"),
    (re.compile(r"\binc\b\.?"), "incorporated"),
    (re.compile(r"\bpty\b\.?"), "proprietary"),
    (re.compile(r"\bco\b\.?"), "company"),
    (re.compile(r"\bassoc\b\.?"), "association"),
    (re.compile(r"\bfdn\b\.?"), "foundation"),
    (re.compile(r"\bgov\b\.?"), "government"),
    (re.compile(r"\baust\b\.?"), "australia"),
    (re.compile(r"\bintl?\b\.?"), "international"),
    (re.compile(r"\buniv\b\.?"), "university"),
]

# Punctuation/noise to strip from org names
_NOISE_CHARS = re.compile(r"[.,;:!'\"()\[\]{}\-/\\&]+")
_MULTI_SPACE = re.compile(r"\s+")


class DuplicateStatus(str, Enum):
    """Status of a duplicate group."""

    PENDING = "pending"
    MERGED = "merged"
    DISMISSED = "dismissed"


def normalise_org_name(name: str) -> str:
    """Normalise an organisation name for deduplication matching."""
    result = name.lower().strip()
    result = _NOISE_CHARS.sub(" ", result)
    for pattern, replacement in _ORG_ABBREVIATIONS:
        result = pattern.sub(replacement, result)
    result = _MULTI_SPACE.sub(" ", result).strip()
    return result


def normalise_title(title: str) -> str:
    """Normalise an opportunity title for deduplication matching."""
    result = title.lower().strip()
    result = _MULTI_SPACE.sub(" ", result)
    return result


def _canonical_key(opportunity: Opportunity) -> str:
    """Generate a deterministic key from normalised org+title."""
    org = normalise_org_name(opportunity.organisation)
    title = normalise_title(opportunity.title)
    combined = f"{org}|{title}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


@dataclass
class DuplicateGroup:
    """A group of opportunities identified as potential duplicates."""

    canonical_id: str
    source_records: list[Opportunity]
    confidence: float = 1.0
    status: DuplicateStatus = DuplicateStatus.PENDING

    @property
    def normalised_org(self) -> str:
        if self.source_records:
            return normalise_org_name(self.source_records[0].organisation)
        return ""

    @property
    def normalised_title(self) -> str:
        if self.source_records:
            return normalise_title(self.source_records[0].title)
        return ""


def find_duplicates(opportunities: list[Opportunity]) -> list[DuplicateGroup]:
    """Group opportunities by normalised org name + title."""
    groups: dict[str, list[Opportunity]] = {}
    for opp in opportunities:
        key = _canonical_key(opp)
        groups.setdefault(key, []).append(opp)

    result: list[DuplicateGroup] = []
    for canonical_id, records in groups.items():
        if len(records) > 1:
            confidence = _compute_confidence(records)
            result.append(
                DuplicateGroup(
                    canonical_id=canonical_id,
                    source_records=records,
                    confidence=confidence,
                )
            )

    result.sort(key=lambda g: g.confidence, reverse=True)
    return result


def _compute_confidence(records: list[Opportunity]) -> float:
    sources = {r.source for r in records}
    if len(sources) > 1:
        return 1.0
    return 0.9


def _completeness_score(opp: Opportunity) -> int:
    score = 0
    if opp.summary:
        score += len(opp.summary)
    if opp.required_skills:
        score += len(opp.required_skills) * 10
    if opp.desirable_skills:
        score += len(opp.desirable_skills) * 5
    if opp.closes_on:
        score += 20
    if opp.fee_aud is not None:
        score += 15
    if opp.url:
        score += 10
    return score


def merge_duplicates(group: DuplicateGroup) -> Opportunity:
    """Merge a duplicate group into a single canonical opportunity."""
    if not group.source_records:
        raise ValueError("Cannot merge an empty duplicate group")

    if len(group.source_records) == 1:
        record = group.source_records[0]
        return Opportunity(
            id=group.canonical_id,
            title=record.title,
            organisation=record.organisation,
            sector=record.sector,
            location=record.location,
            source=record.source,
            url=record.url,
            remuneration=record.remuneration,
            fee_aud=record.fee_aud,
            closes_on=record.closes_on,
            summary=record.summary,
            required_skills=record.required_skills,
            desirable_skills=record.desirable_skills,
        )

    sorted_records = sorted(
        group.source_records, key=_completeness_score, reverse=True
    )
    base = sorted_records[0]

    all_sources = sorted({r.source for r in group.source_records})
    merged_source = "; ".join(all_sources)

    all_required = set(base.required_skills)
    all_desirable = set(base.desirable_skills)
    for record in sorted_records[1:]:
        all_required.update(record.required_skills)
        all_desirable.update(record.desirable_skills)

    summary = base.summary
    if not summary:
        for record in sorted_records[1:]:
            if record.summary:
                summary = record.summary
                break

    closes_on = base.closes_on
    if not closes_on:
        for record in sorted_records[1:]:
            if record.closes_on:
                closes_on = record.closes_on
                break

    fee_aud = base.fee_aud
    if fee_aud is None:
        for record in sorted_records[1:]:
            if record.fee_aud is not None:
                fee_aud = record.fee_aud
                break

    return Opportunity(
        id=group.canonical_id,
        title=base.title,
        organisation=base.organisation,
        sector=base.sector,
        location=base.location,
        source=merged_source,
        url=base.url,
        remuneration=base.remuneration,
        fee_aud=fee_aud,
        closes_on=closes_on,
        summary=summary,
        required_skills=tuple(sorted(all_required)),
        desirable_skills=tuple(sorted(all_desirable)),
    )
