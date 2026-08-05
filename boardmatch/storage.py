"""Abstract file storage for BoardMatch.

Provides a pluggable storage layer via the StorageBackend protocol.
LocalStorageBackend is provided for development/testing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol that any file storage backend must satisfy."""

    def save(self, path: str, data: bytes) -> None:
        """Save data to the given path. Raises IOError on failure."""
        ...

    def delete(self, path: str) -> None:
        """Delete the file at the given path. Raises IOError on failure."""
        ...

    def exists(self, path: str) -> bool:
        """Return True if a file exists at the given path."""
        ...


class LocalStorageBackend:
    """Local filesystem storage backend for development.

    Stores files in a temporary directory that is cleaned up
    when the process exits.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        if base_dir is None:
            self._base_dir = Path(tempfile.mkdtemp(prefix="boardmatch_storage_"))
        else:
            self._base_dir = Path(base_dir)
            self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def save(self, path: str, data: bytes) -> None:
        full_path = self._base_dir / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)

    def delete(self, path: str) -> None:
        full_path = self._base_dir / path
        if full_path.exists():
            full_path.unlink()
        else:
            raise IOError(f"File not found: {path}")

    def exists(self, path: str) -> bool:
        return (self._base_dir / path).exists()
