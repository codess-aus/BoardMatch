"""Document metadata model and repository for BoardMatch.

Handles document lifecycle: upload validation, metadata persistence,
content hash deduplication, and storage coordination.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


class Document(BaseModel):
    """Metadata record for an uploaded document."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    storage_path: str
    status: DocumentStatus = DocumentStatus.PENDING
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def compute_content_hash(data: bytes) -> str:
    """Compute SHA-256 hex digest for content deduplication."""
    return hashlib.sha256(data).hexdigest()


@runtime_checkable
class DocumentRepository(Protocol):
    """Protocol for document metadata persistence."""

    def save(self, document: Document) -> Document: ...

    def get_by_id(self, document_id: str) -> Document | None: ...

    def list_by_user(self, user_id: str) -> list[Document]: ...

    def delete(self, document_id: str) -> bool: ...

    def find_by_hash(self, user_id: str, content_hash: str) -> Document | None: ...


class InMemoryDocumentRepository:
    """In-memory implementation of DocumentRepository for dev/test."""

    def __init__(self) -> None:
        self._store: dict[str, Document] = {}

    def save(self, document: Document) -> Document:
        self._store[document.id] = document
        return document

    def get_by_id(self, document_id: str) -> Document | None:
        return self._store.get(document_id)

    def list_by_user(self, user_id: str) -> list[Document]:
        return [doc for doc in self._store.values() if doc.user_id == user_id]

    def delete(self, document_id: str) -> bool:
        if document_id in self._store:
            del self._store[document_id]
            return True
        return False

    def find_by_hash(self, user_id: str, content_hash: str) -> Document | None:
        for doc in self._store.values():
            if doc.user_id == user_id and doc.content_hash == content_hash:
                return doc
        return None
