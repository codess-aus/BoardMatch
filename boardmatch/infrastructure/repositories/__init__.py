"""Infrastructure repository implementations."""

from boardmatch.infrastructure.repositories.memory import (
    InMemoryCandidateRepository,
    InMemoryOpportunityRepository,
)

__all__ = ["InMemoryCandidateRepository", "InMemoryOpportunityRepository"]
