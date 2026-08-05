"""Document processing endpoints at /api/v1/documents/{document_id}/process*."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ...auth import CurrentUser, get_current_user
from ...document_processing import (
    DocumentProcessor, InMemoryProcessingResultRepository,
    ProcessingStatus, TemplateExtractionProvider,
)
from ...documents import InMemoryDocumentRepository
from .documents import get_document_repo

router = APIRouter(prefix="/documents", tags=["document-processing"])

_processing_repo = InMemoryProcessingResultRepository()
_processor = DocumentProcessor(provider=TemplateExtractionProvider(), result_repo=_processing_repo)


def get_processing_repo() -> InMemoryProcessingResultRepository:
    return _processing_repo


def get_processor() -> DocumentProcessor:
    return _processor


class ExtractedFieldResponse(BaseModel):
    field_name: str
    value: object
    confidence: float
    needs_review: bool


class ProcessingStatusResponse(BaseModel):
    document_id: str
    status: ProcessingStatus
    attempts: int
    error: str | None = None


@router.post("/{document_id}/process", status_code=status.HTTP_202_ACCEPTED, response_model=ProcessingStatusResponse)
def trigger_processing(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    doc_repo: InMemoryDocumentRepository = Depends(get_document_repo),
    processor: DocumentProcessor = Depends(get_processor),
) -> ProcessingStatusResponse:
    """Trigger processing of an uploaded document."""
    doc = doc_repo.get_by_id(document_id)
    if doc is None or doc.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    content = _load_document_content(doc.storage_path)
    result = processor.process(document_id, content=content)
    return ProcessingStatusResponse(document_id=result.document_id, status=result.status, attempts=result.attempts, error=result.error)


@router.get("/{document_id}/processing-status", response_model=ProcessingStatusResponse)
def get_processing_status(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    doc_repo: InMemoryDocumentRepository = Depends(get_document_repo),
    processing_repo: InMemoryProcessingResultRepository = Depends(get_processing_repo),
) -> ProcessingStatusResponse:
    """Check the processing status for a document."""
    doc = doc_repo.get_by_id(document_id)
    if doc is None or doc.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    result = processing_repo.get_by_document_id(document_id)
    if result is None:
        return ProcessingStatusResponse(document_id=document_id, status=ProcessingStatus.PENDING, attempts=0, error=None)
    return ProcessingStatusResponse(document_id=result.document_id, status=result.status, attempts=result.attempts, error=result.error)


@router.get("/{document_id}/extracted-fields", response_model=list[ExtractedFieldResponse])
def get_extracted_fields(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    doc_repo: InMemoryDocumentRepository = Depends(get_document_repo),
    processing_repo: InMemoryProcessingResultRepository = Depends(get_processing_repo),
) -> list[ExtractedFieldResponse]:
    """Get extracted fields from a processed document."""
    doc = doc_repo.get_by_id(document_id)
    if doc is None or doc.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    result = processing_repo.get_by_document_id(document_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document has not been processed yet")
    if result.status != ProcessingStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Document processing status is \'{result.status.value}\', not \'completed\'")
    return [ExtractedFieldResponse(field_name=f.field_name, value=f.value, confidence=f.confidence, needs_review=f.needs_review) for f in result.extracted_fields]


def _load_document_content(storage_path: str) -> str | None:
    """Attempt to load document content from storage."""
    try:
        with open(storage_path, "r") as f:
            return f.read()
    except (FileNotFoundError, IOError, UnicodeDecodeError):
        return None
