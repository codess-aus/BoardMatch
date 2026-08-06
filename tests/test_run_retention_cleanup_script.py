"""Tests for scripts/run_retention_cleanup.py."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from boardmatch.documents import Document, InMemoryDocumentRepository
from boardmatch.retention import InMemoryExtractedTextRepository
from boardmatch.storage import LocalStorageBackend

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "run_retention_cleanup.py"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location("run_retention_cleanup", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def script():
    module = _load_script_module()
    yield module
    sys.modules.pop("run_retention_cleanup", None)


class TestDiscoverUserIds:
    def test_env_var_takes_priority(self, script, monkeypatch):
        monkeypatch.setenv("RETENTION_USER_IDS", "alice, bob ,charlie")
        repo = InMemoryDocumentRepository()
        assert script._discover_user_ids(repo) == ["alice", "bob", "charlie"]

    def test_falls_back_to_in_memory_store(self, script, monkeypatch):
        monkeypatch.delenv("RETENTION_USER_IDS", raising=False)
        repo = InMemoryDocumentRepository()
        repo.save(
            Document(
                id="d1",
                user_id="alice",
                filename="a.pdf",
                content_type="application/pdf",
                size_bytes=1,
                content_hash="h1",
                storage_path="docs/a.pdf",
            )
        )
        repo.save(
            Document(
                id="d2",
                user_id="bob",
                filename="b.pdf",
                content_type="application/pdf",
                size_bytes=1,
                content_hash="h2",
                storage_path="docs/b.pdf",
            )
        )
        assert script._discover_user_ids(repo) == ["alice", "bob"]

    def test_empty_when_nothing_found(self, script, monkeypatch):
        monkeypatch.delenv("RETENTION_USER_IDS", raising=False)
        repo = InMemoryDocumentRepository()
        assert script._discover_user_ids(repo) == []


class TestRun:
    def test_run_with_no_users_is_a_noop_success(self, script, monkeypatch):
        monkeypatch.delenv("RETENTION_USER_IDS", raising=False)
        assert script.run([]) == 0

    def test_run_cleans_up_expired_documents_for_given_users(self, script):
        doc_repo = InMemoryDocumentRepository()
        text_repo = InMemoryExtractedTextRepository()
        storage = LocalStorageBackend()
        storage.save("docs/old.pdf", b"content")
        doc_repo.save(
            Document(
                id="old-doc",
                user_id="alice",
                filename="old.pdf",
                content_type="application/pdf",
                size_bytes=1,
                content_hash="h1",
                storage_path="docs/old.pdf",
                uploaded_at=datetime.now(timezone.utc) - timedelta(days=400),
            )
        )

        exit_code = script.run(
            ["alice"],
            document_repo=doc_repo,
            extracted_text_repo=text_repo,
            storage_backend=storage,
        )
        assert exit_code == 0
        assert doc_repo.get_by_id("old-doc") is None

    def test_run_continues_after_per_user_failure(self, script, monkeypatch):
        text_repo = InMemoryExtractedTextRepository()
        storage = LocalStorageBackend()

        class ExplodingRepo(InMemoryDocumentRepository):
            def list_by_user(self, user_id):
                if user_id == "bad-user":
                    raise RuntimeError("boom")
                return super().list_by_user(user_id)

        exploding_repo = ExplodingRepo()
        exit_code = script.run(
            ["bad-user", "good-user"],
            document_repo=exploding_repo,
            extracted_text_repo=text_repo,
            storage_backend=storage,
        )
        assert exit_code == 0


class TestMain:
    def test_main_with_explicit_user_ids(self, script, monkeypatch):
        monkeypatch.delenv("RETENTION_USER_IDS", raising=False)
        exit_code = script.main(
            ["--user-id", "alice", "--user-id", "bob", "--log-level", "WARNING"]
        )
        assert exit_code == 0

    def test_main_with_no_users_configured(self, script, monkeypatch):
        monkeypatch.delenv("RETENTION_USER_IDS", raising=False)
        exit_code = script.main(["--log-level", "WARNING"])
        assert exit_code == 0
