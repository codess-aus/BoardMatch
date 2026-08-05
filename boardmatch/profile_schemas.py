"""Pydantic request/response models for the profile API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ProfileStatus(StrEnum):
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    CONFIRMED = "confirmed"


class ConnectionSchema(BaseModel):
    name: str
    relationship: str
    organisations: list[str] = []
    board_seats: list[str] = []
    strength: float = Field(default=0.5, ge=0.0, le=1.0)


class ProfileCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    headline: str = ""
    years_experience: int = Field(default=0, ge=0)
    skills: list[str] = []
    sectors: list[str] = []
    credentials: list[str] = []
    board_experience: list[str] = []
    achievements: list[str] = []
    locations: list[str] = []
    connections: list[ConnectionSchema] = []
    status: ProfileStatus = ProfileStatus.DRAFT


class ProfileUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    headline: str = ""
    years_experience: int = Field(default=0, ge=0)
    skills: list[str] = []
    sectors: list[str] = []
    credentials: list[str] = []
    board_experience: list[str] = []
    achievements: list[str] = []
    locations: list[str] = []
    connections: list[ConnectionSchema] = []
    status: ProfileStatus = ProfileStatus.DRAFT


class SkillsUpdateRequest(BaseModel):
    skills: list[str]


class CredentialsUpdateRequest(BaseModel):
    credentials: list[str]


class ExperienceUpdateRequest(BaseModel):
    board_experience: list[str]


class ProfileResponse(BaseModel):
    name: str
    headline: str
    years_experience: int
    skills: list[str]
    sectors: list[str]
    credentials: list[str]
    board_experience: list[str]
    achievements: list[str]
    locations: list[str]
    connections: list[ConnectionSchema]
    status: ProfileStatus
    profile_version: int
