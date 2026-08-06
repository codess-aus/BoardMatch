"""Document processing service for BoardMatch (BM-022).

Parses uploaded CVs into reviewable profile suggestions using a state machine:
pending -> processing -> completed/failed.

Extracted content is mapped to profile fields (skills, experience, credentials)
with confidence scores. Uncertain fields are marked for review.
No extracted data overwrites confirmed profile data automatically.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class ProcessingStatus(str, Enum):
    """State machine for document processing lifecycle."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


VALID_STATUS_TRANSITIONS: dict[ProcessingStatus, set[ProcessingStatus]] = {
    ProcessingStatus.PENDING: {ProcessingStatus.PROCESSING},
    ProcessingStatus.PROCESSING: {ProcessingStatus.COMPLETED, ProcessingStatus.FAILED},
    ProcessingStatus.COMPLETED: set(),
    ProcessingStatus.FAILED: {ProcessingStatus.PENDING},
}


@dataclass
class ExtractedField:
    """A single field extracted from a document with confidence metadata."""

    field_name: str
    value: object
    confidence: float  # 0.0 to 1.0
    needs_review: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass
class ProcessingResult:
    """Result of processing a document, linking raw document to extracted data."""

    document_id: str
    status: ProcessingStatus = ProcessingStatus.PENDING
    extracted_fields: list[ExtractedField] = field(default_factory=list)
    attempts: int = 0
    error: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(self, new_status: ProcessingStatus) -> None:
        """Transition to a new status, enforcing valid state machine transitions."""
        valid = VALID_STATUS_TRANSITIONS.get(self.status, set())
        if new_status not in valid:
            raise InvalidTransitionError(
                f"Cannot transition from {self.status.value} to {new_status.value}"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


REVIEW_THRESHOLD = 0.7
MAX_RETRY_ATTEMPTS = 3

KNOWN_SKILLS = (
    "governance",
    "finance",
    "risk management",
    "cyber security",
    "esg",
    "audit committee",
    "remuneration",
    "digital transformation",
    "regulatory compliance",
    "stakeholder engagement",
    "public policy",
    "people strategy",
    "capital raising",
    "fundraising",
    "strategy",
    "leadership",
    "project management",
    "change management",
    "data analytics",
    "marketing",
)

KNOWN_CREDENTIALS = (
    ("AICD Company Directors Course", "company directors course"),
    ("GAICD", "gaicd"),
    ("CA (ANZ)", "chartered accountant"),
    ("MBA", "mba"),
    ("CPA", "cpa"),
    ("FAICD", "faicd"),
    ("LLB", "llb"),
    ("PhD", "phd"),
)


@runtime_checkable
class ExtractionProvider(Protocol):
    """Protocol for document content extraction backends."""

    def extract(self, document_content: str) -> list[ExtractedField]: ...


class TemplateExtractionProvider:
    """Template-based extraction for demo/testing.

    Looks for keywords in text to extract profile fields.
    In production, replace with AzureDocumentIntelligenceProvider.
    """

    def extract(self, document_content: str) -> list[ExtractedField]:
        fields: list[ExtractedField] = []
        lowered = document_content.lower()

        found_skills = [skill for skill in KNOWN_SKILLS if skill in lowered]
        if found_skills:
            confidence = min(0.9, 0.5 + len(found_skills) * 0.05)
            fields.append(
                ExtractedField(
                    field_name="skills",
                    value=found_skills,
                    confidence=confidence,
                    needs_review=confidence < REVIEW_THRESHOLD,
                )
            )

        match = re.search(r"(\d{1,2})\+?\s*years", lowered)
        if match:
            years = int(match.group(1))
            confidence = 0.8 if years <= 40 else 0.4
            fields.append(
                ExtractedField(
                    field_name="years_experience",
                    value=years,
                    confidence=confidence,
                    needs_review=confidence < REVIEW_THRESHOLD,
                )
            )

        found_credentials = [
            cred for cred, needle in KNOWN_CREDENTIALS if needle in lowered
        ]
        if found_credentials:
            fields.append(
                ExtractedField(
                    field_name="credentials",
                    value=found_credentials,
                    confidence=0.85,
                    needs_review=False,
                )
            )

        lines = [line.strip() for line in document_content.splitlines() if line.strip()]
        if lines:
            name_candidate = lines[0]
            words = name_candidate.split()
            if 1 <= len(words) <= 4 and not any(c.isdigit() for c in name_candidate):
                confidence = 0.75
            else:
                confidence = 0.4
            fields.append(
                ExtractedField(
                    field_name="name",
                    value=name_candidate[:100],
                    confidence=confidence,
                    needs_review=confidence < REVIEW_THRESHOLD,
                )
            )

        headline_match = re.search(
            r"(senior|chief|director|manager|head of|vp|executive|ceo|cfo|cto|coo)[^\n]*",
            lowered,
        )
        if headline_match:
            headline = document_content[
                headline_match.start() : headline_match.end()
            ].strip()
            fields.append(
                ExtractedField(
                    field_name="headline",
                    value=headline[:140],
                    confidence=0.65,
                    needs_review=True,
                )
            )

        sector_keywords = {
            "healthcare": "healthcare",
            "health": "healthcare",
            "financial services": "financial services",
            "banking": "financial services",
            "technology": "technology",
            "education": "education",
            "government": "government",
            "not-for-profit": "not-for-profit",
            "nfp": "not-for-profit",
            "energy": "energy",
            "mining": "mining",
            "retail": "retail",
        }
        found_sectors = list(
            {sector_keywords[k] for k in sector_keywords if k in lowered}
        )
        if found_sectors:
            fields.append(
                ExtractedField(
                    field_name="sectors",
                    value=found_sectors,
                    confidence=0.6,
                    needs_review=True,
                )
            )

        return fields


class AzureDocumentIntelligenceProvider:
    """Extracts CV/document fields using Azure AI Document Intelligence.

    Calls the prebuilt-read model to OCR the raw document text, then maps
    that text to profile fields using ``fallback_provider`` (template-based
    keyword extraction by default). This mirrors production usage where
    Document Intelligence performs OCR on scanned/PDF resumes and downstream
    logic maps the recognized text onto known profile fields.

    A simple circuit breaker protects against repeated transient failures:
    once ``failure_threshold`` consecutive calls fail, the circuit "opens"
    and calls are routed straight to the fallback extractor for
    ``reset_after_seconds`` before a retry is attempted again (half-open).
    If the provider is not configured (no endpoint, or no credential), calls
    always go straight to the fallback rather than raising — so document
    processing never crashes because of Document Intelligence outages.
    """

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        *,
        fallback_provider: ExtractionProvider | None = None,
        failure_threshold: int = 3,
        reset_after_seconds: int = 300,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self._fallback_provider: ExtractionProvider = (
            fallback_provider or TemplateExtractionProvider()
        )
        self._failure_threshold = failure_threshold
        self._reset_after = timedelta(seconds=reset_after_seconds)
        self._consecutive_failures = 0
        self._circuit_open_until: datetime | None = None

    def configured(self) -> bool:
        """True when enough configuration is present to call the real API."""
        return bool(self.endpoint)

    def _circuit_open(self) -> bool:
        if self._circuit_open_until is None:
            return False
        if datetime.now(timezone.utc) >= self._circuit_open_until:
            # Half-open: allow the next call through as a probe.
            self._circuit_open_until = None
            self._consecutive_failures = 0
            return False
        return True

    def _record_failure(self, exc: Exception) -> None:
        self._consecutive_failures += 1
        logger.warning(
            "Azure Document Intelligence call failed (%s/%s consecutive): %s",
            self._consecutive_failures,
            self._failure_threshold,
            exc,
        )
        if self._consecutive_failures >= self._failure_threshold:
            self._circuit_open_until = datetime.now(timezone.utc) + self._reset_after
            logger.warning(
                "Azure Document Intelligence circuit breaker open until %s",
                self._circuit_open_until.isoformat(),
            )

    def extract(self, document_content: str) -> list[ExtractedField]:
        if not self.configured() or self._circuit_open():
            return self._fallback_provider.extract(document_content)

        try:
            recognized_text = self._analyze(document_content)
            self._consecutive_failures = 0
            return self._fallback_provider.extract(recognized_text)
        except Exception as exc:  # noqa: BLE001 - any SDK/network failure
            self._record_failure(exc)
            return self._fallback_provider.extract(document_content)

    def _analyze(self, document_content: str) -> str:
        """Call the Document Intelligence prebuilt-read model and return recognized text."""
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
        from azure.core.credentials import AzureKeyCredential

        if self.api_key:
            credential = AzureKeyCredential(self.api_key)
        else:  # pragma: no cover - requires a real managed identity
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()

        client = DocumentIntelligenceClient(
            endpoint=self.endpoint, credential=credential
        )
        poller = client.begin_analyze_document(
            "prebuilt-read",
            AnalyzeDocumentRequest(bytes_source=document_content.encode("utf-8")),
        )
        result = poller.result()
        return result.content or ""


def create_extraction_provider(settings: object) -> ExtractionProvider:
    """Select an extraction provider based on application settings.

    Uses Azure Document Intelligence when
    ``AZURE_DOC_INTELLIGENCE_ENDPOINT`` is configured (falling back to the
    template extractor on failure via the built-in circuit breaker),
    otherwise uses the template extractor directly.
    """
    endpoint = getattr(settings, "azure_doc_intelligence_endpoint", None)
    if not endpoint:
        return TemplateExtractionProvider()

    api_key_secret = getattr(settings, "azure_doc_intelligence_key", None)
    api_key = api_key_secret.get_secret_value() if api_key_secret else ""
    return AzureDocumentIntelligenceProvider(endpoint=endpoint, api_key=api_key)


@runtime_checkable
class ProcessingResultRepository(Protocol):
    """Protocol for processing result persistence."""

    def save(self, result: ProcessingResult) -> ProcessingResult: ...
    def get_by_document_id(self, document_id: str) -> ProcessingResult | None: ...
    def get_by_id(self, result_id: str) -> ProcessingResult | None: ...


class InMemoryProcessingResultRepository:
    """In-memory implementation of ProcessingResultRepository for dev/test."""

    def __init__(self) -> None:
        self._store: dict[str, ProcessingResult] = {}

    def save(self, result: ProcessingResult) -> ProcessingResult:
        self._store[result.id] = result
        return result

    def get_by_document_id(self, document_id: str) -> ProcessingResult | None:
        for result in self._store.values():
            if result.document_id == document_id:
                return result
        return None

    def get_by_id(self, result_id: str) -> ProcessingResult | None:
        return self._store.get(result_id)


class DocumentProcessor:
    """Orchestrates document processing with retry logic."""

    def __init__(
        self,
        provider: ExtractionProvider | None = None,
        result_repo: ProcessingResultRepository | None = None,
        content_loader: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider = provider or TemplateExtractionProvider()
        self.result_repo = result_repo or InMemoryProcessingResultRepository()
        self._content_loader = content_loader

    def process(self, document_id: str, content: str | None = None) -> ProcessingResult:
        """Process a document, extracting fields from its content."""
        result = self.result_repo.get_by_document_id(document_id)
        if result is None:
            result = ProcessingResult(document_id=document_id)
            self.result_repo.save(result)

        if result.status == ProcessingStatus.COMPLETED:
            return result

        if result.attempts >= MAX_RETRY_ATTEMPTS:
            if result.status != ProcessingStatus.FAILED:
                result.status = ProcessingStatus.FAILED
                result.error = f"Max retry attempts ({MAX_RETRY_ATTEMPTS}) exceeded"
                result.updated_at = datetime.now(timezone.utc)
                self.result_repo.save(result)
            return result

        if result.status == ProcessingStatus.FAILED:
            result.transition_to(ProcessingStatus.PENDING)

        result.transition_to(ProcessingStatus.PROCESSING)
        result.attempts += 1
        self.result_repo.save(result)

        if content is None and self._content_loader:
            content = self._content_loader(document_id)

        if content is None:
            result.transition_to(ProcessingStatus.FAILED)
            result.error = "No content available for processing"
            self.result_repo.save(result)
            return result

        try:
            extracted = self.provider.extract(content)
            result.extracted_fields = extracted
            result.transition_to(ProcessingStatus.COMPLETED)
            result.error = None
        except Exception as exc:  # noqa: BLE001 - any extraction failure marks the doc failed
            result.transition_to(ProcessingStatus.FAILED)
            result.error = str(exc)

        self.result_repo.save(result)
        return result
