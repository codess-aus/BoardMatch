"""Tests for container configuration (BM-032)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


class TestDockerfileSyntax:
    """Verify Dockerfile is well-formed."""

    def test_dockerfile_exists(self) -> None:
        assert DOCKERFILE.exists(), "Dockerfile must exist at repository root"

    def test_dockerfile_has_from_instruction(self) -> None:
        content = DOCKERFILE.read_text()
        assert "FROM" in content, "Dockerfile must contain FROM instruction"

    def test_dockerfile_multi_stage(self) -> None:
        content = DOCKERFILE.read_text()
        from_count = sum(
            1 for line in content.splitlines() if line.strip().startswith("FROM")
        )
        assert from_count >= 2, (
            "Dockerfile must use multi-stage build (at least 2 FROM)"
        )

    def test_dockerfile_has_non_root_user(self) -> None:
        content = DOCKERFILE.read_text()
        assert "USER" in content, "Dockerfile must switch to a non-root USER"
        lines = content.splitlines()
        user_lines = [l.strip() for l in lines if l.strip().startswith("USER")]
        assert any("root" not in l.lower() or "non" in l.lower() for l in user_lines), (
            "Dockerfile USER should not be root"
        )

    def test_dockerfile_has_healthcheck(self) -> None:
        content = DOCKERFILE.read_text()
        assert "HEALTHCHECK" in content, (
            "Dockerfile must have a HEALTHCHECK instruction"
        )

    def test_dockerfile_exposes_port(self) -> None:
        content = DOCKERFILE.read_text()
        assert "EXPOSE 8000" in content, "Dockerfile must EXPOSE 8000"

    def test_dockerfile_has_cmd(self) -> None:
        content = DOCKERFILE.read_text()
        assert "CMD" in content, "Dockerfile must have CMD instruction"
        assert "uvicorn" in content, "CMD should run uvicorn"

    def test_dockerfile_no_mock_data(self) -> None:
        content = DOCKERFILE.read_text()
        assert "mock" not in content.lower() or "no mock" in content.lower(), (
            "Dockerfile must not load mock data"
        )
        assert "seed" not in content.lower(), "Dockerfile must not seed data"


class TestDockerCompose:
    """Verify docker-compose.yml is well-formed."""

    def test_compose_file_exists(self) -> None:
        assert DOCKER_COMPOSE.exists(), "docker-compose.yml must exist"

    def test_compose_has_app_service(self) -> None:
        content = DOCKER_COMPOSE.read_text()
        assert "app:" in content, "docker-compose.yml must define 'app' service"

    def test_compose_has_db_service(self) -> None:
        content = DOCKER_COMPOSE.read_text()
        assert "db:" in content, "docker-compose.yml must define 'db' service"

    def test_compose_has_postgres(self) -> None:
        content = DOCKER_COMPOSE.read_text()
        assert "postgres" in content, "docker-compose.yml must use PostgreSQL"

    def test_compose_has_healthcheck(self) -> None:
        content = DOCKER_COMPOSE.read_text()
        assert "healthcheck" in content, (
            "docker-compose.yml must configure health checks"
        )

    def test_compose_has_volume(self) -> None:
        content = DOCKER_COMPOSE.read_text()
        assert "volumes:" in content, "docker-compose.yml must define volumes"


class TestDockerignore:
    """Verify .dockerignore excludes unnecessary files."""

    def test_dockerignore_exists(self) -> None:
        assert DOCKERIGNORE.exists(), ".dockerignore must exist"

    def test_excludes_git(self) -> None:
        content = DOCKERIGNORE.read_text()
        assert ".git" in content

    def test_excludes_pycache(self) -> None:
        content = DOCKERIGNORE.read_text()
        assert "__pycache__" in content

    def test_excludes_tests(self) -> None:
        content = DOCKERIGNORE.read_text()
        assert "tests" in content.lower()

    def test_excludes_env_file(self) -> None:
        content = DOCKERIGNORE.read_text()
        assert ".env" in content


class TestMigrationScript:
    """Verify migration script exists and is executable."""

    def test_migrate_script_exists(self) -> None:
        script = REPO_ROOT / "scripts" / "migrate.sh"
        assert script.exists(), "scripts/migrate.sh must exist"

    def test_migrate_script_is_executable(self) -> None:
        script = REPO_ROOT / "scripts" / "migrate.sh"
        import os

        assert os.access(script, os.X_OK), "scripts/migrate.sh must be executable"

    def test_migrate_script_requires_database_url(self) -> None:
        content = (REPO_ROOT / "scripts" / "migrate.sh").read_text()
        assert "DATABASE_URL" in content, "migrate.sh must reference DATABASE_URL"


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="Docker not available in this environment",
)
class TestContainerBuild:
    """Integration tests that require Docker (skipped if unavailable)."""

    def test_container_builds_successfully(self) -> None:
        result = subprocess.run(
            ["docker", "build", "-t", "boardmatch-test:latest", "."],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert result.returncode == 0, f"Docker build failed:\n{result.stderr}"

    def test_container_runs_as_non_root(self) -> None:
        result = subprocess.run(
            ["docker", "run", "--rm", "boardmatch-test:latest", "whoami"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert "root" not in result.stdout.strip(), (
            f"Container should not run as root, got: {result.stdout.strip()}"
        )
