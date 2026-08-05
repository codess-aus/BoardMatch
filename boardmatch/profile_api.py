"""Candidate profile CRUD endpoints at /api/v1/profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .auth import CurrentUser, get_current_user
from .infrastructure.repositories.memory import InMemoryCandidateRepository
from .models import Candidate, Connection
from .profile_schemas import (
    ConnectionSchema,
    CredentialsUpdateRequest,
    ExperienceUpdateRequest,
    ProfileCreateRequest,
    ProfileResponse,
    ProfileStatus,
    ProfileUpdateRequest,
    SkillsUpdateRequest,
)

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])

# Module-level in-memory store shared across requests
_candidate_repo = InMemoryCandidateRepository()
_profile_versions: dict[str, int] = {}
_profile_statuses: dict[str, ProfileStatus] = {}


def get_candidate_repo() -> InMemoryCandidateRepository:
    return _candidate_repo


def _build_candidate(data: ProfileCreateRequest | ProfileUpdateRequest) -> Candidate:
    """Convert request model to domain Candidate."""
    connections = [
        Connection(
            name=c.name,
            relationship=c.relationship,
            organisations=tuple(c.organisations),
            board_seats=tuple(c.board_seats),
            strength=c.strength,
        )
        for c in data.connections
    ]
    return Candidate(
        name=data.name,
        headline=data.headline,
        years_experience=data.years_experience,
        skills=list(data.skills),
        sectors=list(data.sectors),
        credentials=list(data.credentials),
        board_experience=list(data.board_experience),
        achievements=list(data.achievements),
        locations=list(data.locations),
        connections=connections,
    )


def _build_response(user_id: str, candidate: Candidate) -> ProfileResponse:
    """Convert domain Candidate to API response."""
    connections = [
        ConnectionSchema(
            name=c.name,
            relationship=c.relationship,
            organisations=list(c.organisations),
            board_seats=list(c.board_seats),
            strength=c.strength,
        )
        for c in candidate.connections
    ]
    return ProfileResponse(
        name=candidate.name,
        headline=candidate.headline,
        years_experience=candidate.years_experience,
        skills=candidate.skills,
        sectors=candidate.sectors,
        credentials=candidate.credentials,
        board_experience=candidate.board_experience,
        achievements=candidate.achievements,
        locations=candidate.locations,
        connections=connections,
        status=_profile_statuses.get(user_id, ProfileStatus.DRAFT),
        profile_version=_profile_versions.get(user_id, 1),
    )


@router.get("", response_model=ProfileResponse)
def get_profile(
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
) -> ProfileResponse:
    """Get the current user's profile."""
    candidate = repo.get_for_user(user.user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    return _build_response(user.user_id, candidate)


@router.put("", response_model=ProfileResponse, status_code=status.HTTP_200_OK)
def put_profile(
    body: ProfileCreateRequest | ProfileUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
) -> ProfileResponse:
    """Create or fully replace the current user's profile."""
    candidate = _build_candidate(body)
    existing = repo.get_for_user(user.user_id)
    if existing is not None:
        _profile_versions[user.user_id] = _profile_versions.get(user.user_id, 1) + 1
    else:
        _profile_versions[user.user_id] = 1

    _profile_statuses[user.user_id] = body.status
    repo.save_for_user(user.user_id, candidate)
    return _build_response(user.user_id, candidate)


@router.patch("/skills", response_model=ProfileResponse)
def patch_skills(
    body: SkillsUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
) -> ProfileResponse:
    """Update only the skills list on the current user's profile."""
    candidate = repo.get_for_user(user.user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    candidate.skills = list(body.skills)
    _profile_versions[user.user_id] = _profile_versions.get(user.user_id, 1) + 1
    repo.save_for_user(user.user_id, candidate)
    return _build_response(user.user_id, candidate)


@router.patch("/credentials", response_model=ProfileResponse)
def patch_credentials(
    body: CredentialsUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
) -> ProfileResponse:
    """Update only the credentials list on the current user's profile."""
    candidate = repo.get_for_user(user.user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    candidate.credentials = list(body.credentials)
    _profile_versions[user.user_id] = _profile_versions.get(user.user_id, 1) + 1
    repo.save_for_user(user.user_id, candidate)
    return _build_response(user.user_id, candidate)


@router.patch("/experience", response_model=ProfileResponse)
def patch_experience(
    body: ExperienceUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryCandidateRepository = Depends(get_candidate_repo),
) -> ProfileResponse:
    """Update only the board experience list on the current user's profile."""
    candidate = repo.get_for_user(user.user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    candidate.board_experience = list(body.board_experience)
    _profile_versions[user.user_id] = _profile_versions.get(user.user_id, 1) + 1
    repo.save_for_user(user.user_id, candidate)
    return _build_response(user.user_id, candidate)
