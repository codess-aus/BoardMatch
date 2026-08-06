"""Document upload and management endpoints at /api/v1/profile/documents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from ...auth import CurrentUser, get_current_user
from ...config import get_settings
from ...documents import (
    MAX_FILE_SIZE_BYTES,
    SUPPORTED_CONTENT_TYPES,
    Document,
    DocumentStatus,
    InMemoryDocumentRepository,
    compute_content_hash,
)
from ...storage import StorageBackend, create_storage_backend

router = APIRouter(prefix="/profile/documents", tags=["documents"])

# Module-level instances (swapped in tests)
_document_repo = InMemoryDocumentRepository()
_storage_backend: StorageBackend | None = None


def get_document_repo() -> InMemoryDocumentRepository:
    return _document_repo


def get_storage_backend() -> StorageBackend:
    """Return the process-wide storage backend, selecting it lazily.

    Selection is deferred to first use (rather than import time) so it can
    be based on live settings — Azure Blob Storage when
    AZURE_STORAGE_ACCOUNT is configured, otherwise local filesystem storage.
    """
    global _storage_backend
    if _storage_backend is None:
        # Deliberately fail closed: if AZURE_STORAGE_ACCOUNT is configured but
        # the client cannot be constructed (bad credentials, network, etc.),
        # propagate the error rather than silently falling back to
        # unencrypted local storage.
        _storage_backend = create_storage_backend(get_settings())
    return _storage_backend


class DocumentResponse(BaseModel):
    id: str
    user_id: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    storage_path: str
    status: DocumentStatus
    uploaded_at: str


def _to_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        user_id=doc.user_id,
        filename=doc.filename,
        content_type=doc.content_type,
        size_bytes=doc.size_bytes,
        content_hash=doc.content_hash,
        storage_path=doc.storage_path,
        status=doc.status,
        uploaded_at=doc.uploaded_at.isoformat(),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentResponse)
async def upload_document(
    file: UploadFile,
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryDocumentRepository = Depends(get_document_repo),
    storage: StorageBackend = Depends(get_storage_backend),
) -> DocumentResponse:
    """Upload a document (PDF or Word)."""
    # Validate content type
    content_type = file.content_type or ""
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {content_type}. "
            f"Supported types: {', '.join(sorted(SUPPORTED_CONTENT_TYPES))}",
        )

    # Read file content
    data = await file.read()

    # Validate file size
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size {len(data)} bytes exceeds maximum of {MAX_FILE_SIZE_BYTES} bytes (10MB).",
        )

    # Compute content hash for deduplication
    content_hash = compute_content_hash(data)
    existing = repo.find_by_hash(user.user_id, content_hash)
    if existing is not None:
        return _to_response(existing)

    # Store the file
    filename = file.filename or "unnamed"
    storage_path = f"{user.user_id}/{content_hash}/{filename}"
    try:
        storage.save(storage_path, data)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Storage failure: {exc}",
        )

    # Persist metadata
    document = Document(
        user_id=user.user_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        content_hash=content_hash,
        storage_path=storage_path,
        status=DocumentStatus.PENDING,
    )
    repo.save(document)
    return _to_response(document)


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryDocumentRepository = Depends(get_document_repo),
) -> list[DocumentResponse]:
    """List all documents for the current user."""
    docs = repo.list_by_user(user.user_id)
    return [_to_response(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryDocumentRepository = Depends(get_document_repo),
) -> DocumentResponse:
    """Get metadata for a specific document."""
    doc = repo.get_by_id(document_id)
    if doc is None or doc.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return _to_response(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    repo: InMemoryDocumentRepository = Depends(get_document_repo),
    storage: StorageBackend = Depends(get_storage_backend),
) -> None:
    """Delete a document and its stored file."""
    doc = repo.get_by_id(document_id)
    if doc is None or doc.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Remove from storage (best effort)
    try:
        storage.delete(doc.storage_path)
    except OSError:
        pass  # File may already be gone

    repo.delete(document_id)
