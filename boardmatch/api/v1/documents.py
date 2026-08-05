"""Document upload and management endpoints at /api/v1/profile/documents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from ...auth import CurrentUser, get_current_user
from ...documents import (
    SUPPORTED_CONTENT_TYPES,
    MAX_FILE_SIZE_BYTES,
    Document,
    DocumentStatus,
    InMemoryDocumentRepository,
    compute_content_hash,
)
from ...storage import LocalStorageBackend, StorageBackend

router = APIRouter(prefix="/profile/documents", tags=["documents"])

# Module-level instances (swapped in tests)
_document_repo = InMemoryDocumentRepository()
_storage_backend: StorageBackend = LocalStorageBackend()


def get_document_repo() -> InMemoryDocumentRepository:
    return _document_repo


def get_storage_backend() -> StorageBackend:
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
    except (IOError, OSError) as exc:
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
    except (IOError, OSError):
        pass  # File may already be gone

    repo.delete(document_id)
