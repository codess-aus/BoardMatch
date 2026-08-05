"""v1 Network routes for managing user connections and intro paths."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from boardmatch.auth import CurrentUser, get_required_user
from boardmatch.integrations import (
    IntegrationRepository,
    IntegrationStatus,
)
from boardmatch.models import Candidate, Connection
from boardmatch.network import paths_for

from boardmatch.api.v1.integrations import get_repository as get_integration_repository

router = APIRouter(prefix="/network", tags=["network"])


class NetworkConnection(BaseModel):
    """A user's network connection with approval and strength metadata."""

    id: str
    user_id: str
    name: str
    relationship: str
    organisations: list[str] = Field(default_factory=list)
    board_seats: list[str] = Field(default_factory=list)
    approved: bool = False
    strength: int = Field(default=5, ge=1, le=10)
    source: str = "manual"
    deleted: bool = False


class InMemoryNetworkRepository:
    """In-memory repository for network connections."""

    def __init__(self) -> None:
        self._connections: dict[str, NetworkConnection] = {}

    def list_by_user(self, user_id: str) -> list[NetworkConnection]:
        return [
            c
            for c in self._connections.values()
            if c.user_id == user_id and not c.deleted
        ]

    def get(self, connection_id: str) -> NetworkConnection | None:
        conn = self._connections.get(connection_id)
        if conn and not conn.deleted:
            return conn
        return None

    def save(self, connection: NetworkConnection) -> None:
        self._connections[connection.id] = connection

    def delete(self, connection_id: str) -> bool:
        conn = self._connections.get(connection_id)
        if conn and not conn.deleted:
            conn.deleted = True
            return True
        return False


_network_repo = InMemoryNetworkRepository()


def get_network_repository() -> InMemoryNetworkRepository:
    """Dependency for the network repository."""
    return _network_repo


_FIXTURE_CONNECTIONS = [
    {
        "name": "Sarah Chen",
        "relationship": "Former colleague",
        "organisations": ["TechCorp Australia"],
        "board_seats": ["ASX FinTech Ltd"],
        "source": "microsoft_graph",
    },
    {
        "name": "James Morton",
        "relationship": "Board search firm contact",
        "organisations": ["Morton & Associates"],
        "board_seats": [],
        "source": "microsoft_graph",
    },
    {
        "name": "Priya Patel",
        "relationship": "Industry peer",
        "organisations": ["GreenEnergy Co"],
        "board_seats": ["GreenEnergy Co", "CleanTech Fund"],
        "source": "microsoft_graph",
    },
]


class NetworkConnectionResponse(BaseModel):
    id: str
    name: str
    relationship: str
    organisations: list[str]
    board_seats: list[str]
    approved: bool
    strength: int
    source: str


class NetworkConnectionListResponse(BaseModel):
    connections: list[NetworkConnectionResponse]


class SyncResponse(BaseModel):
    imported: int
    connections: list[NetworkConnectionResponse]


class ConnectionUpdateRequest(BaseModel):
    approved: Optional[bool] = None
    strength: Optional[int] = Field(default=None, ge=1, le=10)


class IntroPathItemResponse(BaseModel):
    connection_id: str
    connection_name: str
    relationship: str
    reason: str
    warmth: int


class IntroPathsResponse(BaseModel):
    opportunity_id: str
    paths: list[IntroPathItemResponse]


def _to_response(conn: NetworkConnection) -> NetworkConnectionResponse:
    return NetworkConnectionResponse(
        id=conn.id,
        name=conn.name,
        relationship=conn.relationship,
        organisations=conn.organisations,
        board_seats=conn.board_seats,
        approved=conn.approved,
        strength=conn.strength,
        source=conn.source,
    )


@router.get("/connections", response_model=NetworkConnectionListResponse)
def list_connections(
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryNetworkRepository = Depends(get_network_repository),
) -> NetworkConnectionListResponse:
    """List the authenticated user's network connections."""
    connections = repo.list_by_user(user.user_id)
    return NetworkConnectionListResponse(
        connections=[_to_response(c) for c in connections]
    )


@router.post("/sync", response_model=SyncResponse, status_code=status.HTTP_200_OK)
def sync_connections(
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryNetworkRepository = Depends(get_network_repository),
    integration_repo: IntegrationRepository = Depends(get_integration_repository),
) -> SyncResponse:
    """Trigger a sync from Microsoft Graph. Requires active Microsoft consent."""
    integration = integration_repo.get(user.user_id, "microsoft")
    if integration is None or integration.status != IntegrationStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active Microsoft integration consent required",
        )

    imported: list[NetworkConnection] = []
    for fixture in _FIXTURE_CONNECTIONS:
        conn = NetworkConnection(
            id=str(uuid.uuid4()),
            user_id=user.user_id,
            name=fixture["name"],
            relationship=fixture["relationship"],
            organisations=fixture["organisations"],
            board_seats=fixture["board_seats"],
            approved=False,
            strength=5,
            source=fixture["source"],
        )
        repo.save(conn)
        imported.append(conn)

    return SyncResponse(
        imported=len(imported),
        connections=[_to_response(c) for c in imported],
    )


@router.patch("/connections/{connection_id}", response_model=NetworkConnectionResponse)
def update_connection(
    connection_id: str,
    body: ConnectionUpdateRequest,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryNetworkRepository = Depends(get_network_repository),
) -> NetworkConnectionResponse:
    """Update a connection (approve, adjust strength)."""
    conn = repo.get(connection_id)
    if conn is None or conn.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found",
        )

    if body.approved is not None:
        conn.approved = body.approved
    if body.strength is not None:
        conn.strength = body.strength
    repo.save(conn)
    return _to_response(conn)


@router.delete(
    "/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_connection(
    connection_id: str,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryNetworkRepository = Depends(get_network_repository),
) -> None:
    """Remove a connection. Deleted connections are excluded from intro paths."""
    conn = repo.get(connection_id)
    if conn is None or conn.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found",
        )
    repo.delete(connection_id)


intro_router = APIRouter(tags=["network"])


@intro_router.get(
    "/opportunities/{opportunity_id}/intro-paths",
    response_model=IntroPathsResponse,
)
def get_intro_paths(
    opportunity_id: str,
    user: CurrentUser = Depends(get_required_user),
    repo: InMemoryNetworkRepository = Depends(get_network_repository),
) -> IntroPathsResponse:
    """Get ranked intro paths for an opportunity using only approved connections."""
    from boardmatch import discovery

    opportunity = discovery.get_opportunity(opportunity_id)
    if opportunity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found",
        )

    approved_connections = [
        c for c in repo.list_by_user(user.user_id) if c.approved
    ]

    domain_connections = [
        Connection(
            name=c.name,
            relationship=c.relationship,
            organisations=tuple(c.organisations),
            board_seats=tuple(c.board_seats),
            strength=c.strength / 10.0,
        )
        for c in approved_connections
    ]

    candidate = Candidate(
        name="user",
        connections=domain_connections,
    )

    paths = paths_for(candidate, opportunity)

    conn_by_name = {c.name: c for c in approved_connections}
    result_paths = []
    for path in paths:
        network_conn = conn_by_name.get(path.connection.name)
        if network_conn:
            result_paths.append(
                IntroPathItemResponse(
                    connection_id=network_conn.id,
                    connection_name=path.connection.name,
                    relationship=path.connection.relationship,
                    reason=path.reason,
                    warmth=path.warmth,
                )
            )

    return IntroPathsResponse(
        opportunity_id=opportunity_id,
        paths=result_paths,
    )
