"""Admin API endpoints for deduplication management."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import discovery
from ...ingestion.deduplication import (
    DuplicateGroup,
    DuplicateStatus,
    find_duplicates,
    merge_duplicates,
)
from ...models import Opportunity

router = APIRouter(prefix="/admin/duplicates", tags=["admin", "deduplication"])

_duplicate_groups: dict[str, DuplicateGroup] = {}


class SourceRecordResponse(BaseModel):
    id: str
    title: str
    organisation: str
    source: str
    url: str


class DuplicateGroupResponse(BaseModel):
    canonical_id: str
    normalised_org: str
    normalised_title: str
    confidence: float
    status: str
    source_records: list[SourceRecordResponse]


class DuplicateGroupListResponse(BaseModel):
    count: int
    groups: list[DuplicateGroupResponse]


class MergeResultResponse(BaseModel):
    canonical_id: str
    merged_opportunity_id: str
    title: str
    organisation: str
    source: str
    records_merged: int


class DismissResponse(BaseModel):
    canonical_id: str
    status: str


def _group_to_response(group: DuplicateGroup) -> DuplicateGroupResponse:
    return DuplicateGroupResponse(
        canonical_id=group.canonical_id,
        normalised_org=group.normalised_org,
        normalised_title=group.normalised_title,
        confidence=group.confidence,
        status=group.status.value,
        source_records=[
            SourceRecordResponse(
                id=r.id,
                title=r.title,
                organisation=r.organisation,
                source=r.source,
                url=r.url,
            )
            for r in group.source_records
        ],
    )


def _refresh_duplicates() -> None:
    global _duplicate_groups
    opportunities = discovery.discover()
    groups = find_duplicates(opportunities)
    for group in groups:
        if group.canonical_id in _duplicate_groups:
            existing = _duplicate_groups[group.canonical_id]
            group.status = existing.status
    _duplicate_groups = {g.canonical_id: g for g in groups}


@router.get("", response_model=DuplicateGroupListResponse)
def list_duplicates(
    status: Optional[str] = None,
) -> DuplicateGroupListResponse:
    """List potential duplicate groups, optionally filtered by status."""
    _refresh_duplicates()
    groups = list(_duplicate_groups.values())
    if status:
        try:
            filter_status = DuplicateStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Must be one of: pending, merged, dismissed",
            )
        groups = [g for g in groups if g.status == filter_status]
    return DuplicateGroupListResponse(
        count=len(groups),
        groups=[_group_to_response(g) for g in groups],
    )


@router.post("/{group_id}/merge", response_model=MergeResultResponse)
def merge_duplicate_group(group_id: str) -> MergeResultResponse:
    """Confirm and merge a duplicate group into a canonical opportunity."""
    _refresh_duplicates()
    group = _duplicate_groups.get(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Duplicate group not found")
    if group.status == DuplicateStatus.MERGED:
        raise HTTPException(status_code=409, detail="Group already merged")
    merged = merge_duplicates(group)
    group.status = DuplicateStatus.MERGED
    _duplicate_groups[group_id] = group
    return MergeResultResponse(
        canonical_id=group_id,
        merged_opportunity_id=merged.id,
        title=merged.title,
        organisation=merged.organisation,
        source=merged.source,
        records_merged=len(group.source_records),
    )


@router.post("/{group_id}/dismiss", response_model=DismissResponse)
def dismiss_duplicate_group(group_id: str) -> DismissResponse:
    """Dismiss a duplicate group (mark as not a true duplicate)."""
    _refresh_duplicates()
    group = _duplicate_groups.get(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Duplicate group not found")
    group.status = DuplicateStatus.DISMISSED
    _duplicate_groups[group_id] = group
    return DismissResponse(
        canonical_id=group_id,
        status=group.status.value,
    )
