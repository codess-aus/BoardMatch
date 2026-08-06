"""Tests for the document upload workflow (BM-010)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from boardmatch.api import app
from boardmatch.api.v1.documents import (
    _document_repo,
    get_storage_backend,
)
from boardmatch.documents import (
    MAX_FILE_SIZE_BYTES,
    compute_content_hash,
)
from boardmatch.storage import StorageBackend


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear document repo between tests."""
    _document_repo._store.clear()
    yield
    _document_repo._store.clear()


@pytest.fixture
def mock_storage():
    """Provide a mock storage backend."""
    storage = MagicMock(spec=StorageBackend)
    storage.save = MagicMock(return_value=None)
    storage.delete = MagicMock(return_value=None)
    storage.exists = MagicMock(return_value=True)
    return storage


@pytest.fixture
def client(mock_storage):
    """TestClient with mocked storage backend."""
    app.dependency_overrides[get_storage_backend] = lambda: mock_storage
    yield TestClient(app)
    app.dependency_overrides.pop(get_storage_backend, None)


def _headers(user_id: str = "user-001") -> dict[str, str]:
    return {"X-Dev-User-Id": user_id}


def _pdf_content() -> bytes:
    return b"%PDF-1.4 fake pdf content for testing"


def _upload_file(
    client: TestClient,
    content: bytes | None = None,
    filename: str = "resume.pdf",
    content_type: str = "application/pdf",
    user_id: str = "user-001",
):
    """Helper to upload a file."""
    if content is None:
        content = _pdf_content()
    return client.post(
        "/api/v1/profile/documents",
        headers=_headers(user_id),
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


class TestUploadDocument:
    """Tests for POST /api/v1/profile/documents."""

    def test_valid_pdf_upload(self, client, mock_storage):
        """Valid PDF upload returns 201 with metadata."""
        content = _pdf_content()
        resp = _upload_file(client, content=content)

        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "resume.pdf"
        assert data["content_type"] == "application/pdf"
        assert data["size_bytes"] == len(content)
        assert data["content_hash"] == compute_content_hash(content)
        assert data["status"] == "pending"
        assert data["user_id"] == "user-001"
        mock_storage.save.assert_called_once()

    def test_valid_docx_upload(self, client, mock_storage):
        """Valid DOCX upload returns 201."""
        content = b"PK fake docx content"
        resp = _upload_file(
            client,
            content=content,
            filename="resume.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        assert resp.status_code == 201
        assert resp.json()["content_type"] == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_unsupported_type_rejection(self, client, mock_storage):
        """Unsupported content type returns 415."""
        resp = _upload_file(
            client,
            content=b"not an image",
            filename="photo.png",
            content_type="image/png",
        )
        assert resp.status_code == 415
        assert "Unsupported file type" in resp.json()["message"]
        mock_storage.save.assert_not_called()

    def test_size_limit_rejection(self, client, mock_storage):
        """File exceeding 10MB returns 413."""
        large_content = b"x" * (MAX_FILE_SIZE_BYTES + 1)
        resp = _upload_file(client, content=large_content)

        assert resp.status_code == 413
        assert "exceeds maximum" in resp.json()["message"]
        mock_storage.save.assert_not_called()

    def test_duplicate_file_detection(self, client, mock_storage):
        """Uploading same content twice returns existing document."""
        content = _pdf_content()

        resp1 = _upload_file(client, content=content)
        assert resp1.status_code == 201
        doc_id1 = resp1.json()["id"]

        resp2 = _upload_file(client, content=content, filename="resume_v2.pdf")
        # Returns existing doc (dedup), not a new one
        assert resp2.status_code == 201
        assert resp2.json()["id"] == doc_id1
        # Storage.save called only once (first upload)
        assert mock_storage.save.call_count == 1

    def test_storage_failure_handling(self, client, mock_storage):
        """Storage failure returns 500."""
        mock_storage.save.side_effect = OSError("Disk full")
        resp = _upload_file(client)

        assert resp.status_code == 500
        assert "Storage failure" in resp.json()["message"]


class TestListDocuments:
    """Tests for GET /api/v1/profile/documents."""

    def test_list_empty(self, client):
        """Empty list when no documents uploaded."""
        resp = client.get("/api/v1/profile/documents", headers=_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_documents(self, client):
        """List returns all user documents."""
        _upload_file(client, content=b"%PDF-1 doc1", filename="doc1.pdf")
        _upload_file(client, content=b"%PDF-1 doc2", filename="doc2.pdf")

        resp = client.get("/api/v1/profile/documents", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        filenames = {d["filename"] for d in data}
        assert filenames == {"doc1.pdf", "doc2.pdf"}


class TestGetDocument:
    """Tests for GET /api/v1/profile/documents/{document_id}."""

    def test_get_existing_document(self, client):
        """Get metadata for a specific document."""
        resp = _upload_file(client)
        doc_id = resp.json()["id"]

        resp = client.get(f"/api/v1/profile/documents/{doc_id}", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["id"] == doc_id

    def test_get_nonexistent_document(self, client):
        """Requesting non-existent document returns 404."""
        resp = client.get(
            "/api/v1/profile/documents/nonexistent-id", headers=_headers()
        )
        assert resp.status_code == 404


class TestDeleteDocument:
    """Tests for DELETE /api/v1/profile/documents/{document_id}."""

    def test_delete_document(self, client, mock_storage):
        """Deleting a document returns 204 and removes it."""
        resp = _upload_file(client)
        doc_id = resp.json()["id"]

        del_resp = client.delete(
            f"/api/v1/profile/documents/{doc_id}", headers=_headers()
        )
        assert del_resp.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/v1/profile/documents/{doc_id}", headers=_headers())
        assert get_resp.status_code == 404
        mock_storage.delete.assert_called_once()

    def test_delete_nonexistent(self, client):
        """Deleting non-existent document returns 404."""
        resp = client.delete(
            "/api/v1/profile/documents/nonexistent-id", headers=_headers()
        )
        assert resp.status_code == 404


class TestUserIsolation:
    """Ensure documents are isolated per user."""

    def test_user_cannot_see_other_user_documents(self, client):
        """User A's documents are not visible to User B."""
        _upload_file(client, user_id="user-a", content=b"%PDF user-a doc")

        resp = client.get("/api/v1/profile/documents", headers=_headers("user-b"))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_user_cannot_get_other_user_document(self, client):
        """User B cannot access User A's document by ID."""
        resp = _upload_file(client, user_id="user-a", content=b"%PDF user-a doc")
        doc_id = resp.json()["id"]

        get_resp = client.get(
            f"/api/v1/profile/documents/{doc_id}", headers=_headers("user-b")
        )
        assert get_resp.status_code == 404

    def test_user_cannot_delete_other_user_document(self, client):
        """User B cannot delete User A's document."""
        resp = _upload_file(client, user_id="user-a", content=b"%PDF user-a doc")
        doc_id = resp.json()["id"]

        del_resp = client.delete(
            f"/api/v1/profile/documents/{doc_id}", headers=_headers("user-b")
        )
        assert del_resp.status_code == 404
