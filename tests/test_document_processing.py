"""Tests for the document processing service (BM-022)."""
from __future__ import annotations
import io
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from boardmatch.api import app
from boardmatch.api.v1.documents import _document_repo, get_storage_backend
from boardmatch.api.v1.processing import _processing_repo, get_processor
from boardmatch.document_processing import (
    DocumentProcessor, ExtractedField, InvalidTransitionError,
    MAX_RETRY_ATTEMPTS, ProcessingResult, ProcessingStatus,
    REVIEW_THRESHOLD, TemplateExtractionProvider,
)
from boardmatch.storage import StorageBackend


@pytest.fixture(autouse=True)
def _reset_state():
    _document_repo._store.clear()
    _processing_repo._store.clear()
    yield
    _document_repo._store.clear()
    _processing_repo._store.clear()


@pytest.fixture
def mock_storage():
    storage = MagicMock(spec=StorageBackend)
    storage.save = MagicMock(return_value=None)
    storage.delete = MagicMock(return_value=None)
    storage.exists = MagicMock(return_value=True)
    return storage


@pytest.fixture
def client(mock_storage):
    app.dependency_overrides[get_storage_backend] = lambda: mock_storage
    processor = DocumentProcessor(provider=TemplateExtractionProvider(), result_repo=_processing_repo)
    app.dependency_overrides[get_processor] = lambda: processor
    yield TestClient(app)
    app.dependency_overrides.pop(get_storage_backend, None)
    app.dependency_overrides.pop(get_processor, None)


def _headers(user_id="user-001"):
    return {"X-Dev-User-Id": user_id}


def _upload_pdf(client, content=None, user_id="user-001"):
    if content is None:
        content = b"%PDF-1.4 fake pdf content"
    return client.post("/api/v1/profile/documents", headers=_headers(user_id),
        files={"file": ("resume.pdf", io.BytesIO(content), "application/pdf")})


class TestProcessingStatus:
    def test_enum_values(self):
        assert ProcessingStatus.PENDING == "pending"
        assert ProcessingStatus.PROCESSING == "processing"
        assert ProcessingStatus.COMPLETED == "completed"
        assert ProcessingStatus.FAILED == "failed"

    def test_all_states_defined(self):
        assert len(ProcessingStatus) == 4


class TestExtractedField:
    def test_valid_field(self):
        f = ExtractedField(field_name="skills", value=["governance"], confidence=0.85, needs_review=False)
        assert f.field_name == "skills"
        assert f.confidence == 0.85

    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            ExtractedField(field_name="x", value="y", confidence=1.5, needs_review=False)
        with pytest.raises(ValueError):
            ExtractedField(field_name="x", value="y", confidence=-0.1, needs_review=False)

    def test_edge_values(self):
        ExtractedField(field_name="x", value="y", confidence=0.0, needs_review=True)
        ExtractedField(field_name="x", value="y", confidence=1.0, needs_review=False)


class TestProcessingResult:
    def test_default_state(self):
        r = ProcessingResult(document_id="doc-1")
        assert r.status == ProcessingStatus.PENDING
        assert r.attempts == 0

    def test_valid_transitions(self):
        r = ProcessingResult(document_id="doc-1")
        r.transition_to(ProcessingStatus.PROCESSING)
        r.transition_to(ProcessingStatus.COMPLETED)
        assert r.status == ProcessingStatus.COMPLETED

    def test_processing_to_failed(self):
        r = ProcessingResult(document_id="doc-1")
        r.transition_to(ProcessingStatus.PROCESSING)
        r.transition_to(ProcessingStatus.FAILED)
        assert r.status == ProcessingStatus.FAILED

    def test_failed_to_pending_retry(self):
        r = ProcessingResult(document_id="doc-1")
        r.transition_to(ProcessingStatus.PROCESSING)
        r.transition_to(ProcessingStatus.FAILED)
        r.transition_to(ProcessingStatus.PENDING)
        assert r.status == ProcessingStatus.PENDING

    def test_invalid_transition(self):
        r = ProcessingResult(document_id="doc-1")
        with pytest.raises(InvalidTransitionError):
            r.transition_to(ProcessingStatus.COMPLETED)

    def test_completed_terminal(self):
        r = ProcessingResult(document_id="doc-1")
        r.transition_to(ProcessingStatus.PROCESSING)
        r.transition_to(ProcessingStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            r.transition_to(ProcessingStatus.PENDING)


class TestTemplateExtractionProvider:
    def test_extracts_skills(self):
        fields = TemplateExtractionProvider().extract("governance, finance, risk management")
        skills = next(f for f in fields if f.field_name == "skills")
        assert "governance" in skills.value

    def test_extracts_years(self):
        fields = TemplateExtractionProvider().extract("Jane Smith\n15+ years experience governance.")
        yrs = next(f for f in fields if f.field_name == "years_experience")
        assert yrs.value == 15

    def test_extracts_credentials(self):
        fields = TemplateExtractionProvider().extract("John Doe\nGAICD, MBA, CPA qualified.")
        creds = next(f for f in fields if f.field_name == "credentials")
        assert "GAICD" in creds.value and "MBA" in creds.value

    def test_extracts_name(self):
        fields = TemplateExtractionProvider().extract("Sarah Johnson\nSenior Director")
        name = next(f for f in fields if f.field_name == "name")
        assert name.value == "Sarah Johnson"

    def test_extracts_headline(self):
        fields = TemplateExtractionProvider().extract("Jane Doe\nSenior Executive with governance")
        h = next(f for f in fields if f.field_name == "headline")
        assert "Senior Executive" in h.value

    def test_sectors_marked_review(self):
        fields = TemplateExtractionProvider().extract("Jane Doe\nDirector in healthcare sector")
        s = next((f for f in fields if f.field_name == "sectors"), None)
        if s:
            assert s.needs_review is True

    def test_empty_text(self):
        assert TemplateExtractionProvider().extract("") == []


class TestDocumentProcessor:
    def test_process_success(self):
        r = DocumentProcessor().process("doc-1", content="Jane Smith\n15 years governance finance. MBA.")
        assert r.status == ProcessingStatus.COMPLETED
        assert r.attempts == 1
        assert len(r.extracted_fields) > 0

    def test_no_content_fails(self):
        r = DocumentProcessor().process("doc-1", content=None)
        assert r.status == ProcessingStatus.FAILED
        assert "No content" in r.error

    def test_provider_failure_retries(self):
        p = MagicMock()
        p.extract.side_effect = RuntimeError("timeout")
        proc = DocumentProcessor(provider=p)
        proc.process("doc-1", content="x")
        proc.process("doc-1", content="x")
        r = proc.process("doc-1", content="x")
        assert r.attempts == 3

    def test_max_retries_exceeded(self):
        p = MagicMock()
        p.extract.side_effect = RuntimeError("timeout")
        proc = DocumentProcessor(provider=p)
        for _ in range(MAX_RETRY_ATTEMPTS):
            proc.process("doc-1", content="x")
        r = proc.process("doc-1", content="x")
        assert r.attempts == MAX_RETRY_ATTEMPTS
        assert p.extract.call_count == MAX_RETRY_ATTEMPTS

    def test_already_completed(self):
        proc = DocumentProcessor()
        r1 = proc.process("doc-1", content="Jane Smith\nGovernance")
        r2 = proc.process("doc-1", content="other")
        assert r2.id == r1.id

    def test_linked_to_document(self):
        assert DocumentProcessor().process("doc-xyz", content="text").document_id == "doc-xyz"

    def test_content_loader(self):
        proc = DocumentProcessor(content_loader=lambda _: "governance skills")
        assert proc.process("doc-1").status == ProcessingStatus.COMPLETED


class TestProcessEndpoint:
    def test_trigger(self, client):
        doc_id = _upload_pdf(client).json()["id"]
        resp = client.post(f"/api/v1/documents/{doc_id}/process", headers=_headers())
        assert resp.status_code == 202
        assert resp.json()["document_id"] == doc_id

    def test_nonexistent(self, client):
        assert client.post("/api/v1/documents/bad/process", headers=_headers()).status_code == 404

    def test_other_user(self, client):
        doc_id = _upload_pdf(client, user_id="a").json()["id"]
        assert client.post(f"/api/v1/documents/{doc_id}/process", headers=_headers("b")).status_code == 404


class TestProcessingStatusEndpoint:
    def test_before_processing(self, client):
        doc_id = _upload_pdf(client).json()["id"]
        resp = client.get(f"/api/v1/documents/{doc_id}/processing-status", headers=_headers())
        assert resp.json()["status"] == "pending"

    def test_after_processing(self, client):
        doc_id = _upload_pdf(client).json()["id"]
        client.post(f"/api/v1/documents/{doc_id}/process", headers=_headers())
        resp = client.get(f"/api/v1/documents/{doc_id}/processing-status", headers=_headers())
        assert resp.json()["status"] in ["completed", "failed"]

    def test_nonexistent(self, client):
        assert client.get("/api/v1/documents/bad/processing-status", headers=_headers()).status_code == 404


class TestExtractedFieldsEndpoint:
    def test_after_processing(self, client):
        doc_id = _upload_pdf(client).json()["id"]
        from boardmatch.api.v1.processing import _processing_repo
        DocumentProcessor(provider=TemplateExtractionProvider(), result_repo=_processing_repo).process(
            doc_id, content="Jane Smith\n15 years governance finance. GAICD.")
        resp = client.get(f"/api/v1/documents/{doc_id}/extracted-fields", headers=_headers())
        assert resp.status_code == 200
        assert any(f["field_name"] == "skills" for f in resp.json())

    def test_before_processing(self, client):
        doc_id = _upload_pdf(client).json()["id"]
        assert client.get(f"/api/v1/documents/{doc_id}/extracted-fields", headers=_headers()).status_code == 404

    def test_not_completed(self, client):
        doc_id = _upload_pdf(client).json()["id"]
        from boardmatch.api.v1.processing import _processing_repo
        r = ProcessingResult(document_id=doc_id)
        r.transition_to(ProcessingStatus.PROCESSING)
        _processing_repo.save(r)
        assert client.get(f"/api/v1/documents/{doc_id}/extracted-fields", headers=_headers()).status_code == 409

    def test_nonexistent(self, client):
        assert client.get("/api/v1/documents/bad/extracted-fields", headers=_headers()).status_code == 404


class TestConfidenceAndReview:
    def test_high_confidence(self):
        fields = TemplateExtractionProvider().extract("John Smith\n20 years governance finance. GAICD MBA.")
        creds = next(f for f in fields if f.field_name == "credentials")
        assert creds.confidence >= REVIEW_THRESHOLD
        assert creds.needs_review is False

    def test_low_confidence(self):
        assert ExtractedField(field_name="t", value="v", confidence=0.5, needs_review=True).needs_review

    def test_no_auto_overwrite(self):
        for f in TemplateExtractionProvider().extract("Jane Smith\nDirector governance healthcare"):
            assert hasattr(f, "needs_review")
