"""Validate the CI and Deploy workflow YAML structures."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _load_workflow(name: str) -> dict:
    path = WORKFLOWS_DIR / name
    assert path.exists(), f"Workflow file not found: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML parses the YAML key `on` as boolean True; normalize it.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


class TestCIWorkflow:
    """Validate ci.yml structure."""

    def setup_method(self) -> None:
        self.workflow = _load_workflow("ci.yml")

    def test_triggers_on_pull_request(self) -> None:
        assert "pull_request" in self.workflow["on"]

    def test_targets_main_branch(self) -> None:
        pr_config = self.workflow["on"]["pull_request"]
        assert "main" in pr_config["branches"]

    def test_has_test_job(self) -> None:
        assert "test" in self.workflow["jobs"]

    def test_uses_python_312(self) -> None:
        test_job = self.workflow["jobs"]["test"]
        steps = test_job["steps"]
        python_step = next(
            (s for s in steps if s.get("uses", "").startswith("actions/setup-python")),
            None,
        )
        assert python_step is not None
        assert python_step["with"]["python-version"] == "3.12"

    def test_runs_ruff_check(self) -> None:
        test_job = self.workflow["jobs"]["test"]
        all_run_steps = " ".join(
            s.get("run", "") for s in test_job["steps"] if "run" in s
        )
        assert "ruff check" in all_run_steps

    def test_runs_full_default_ruff_rules(self) -> None:
        """Ruff check should run against the full default rule set, not just
        the narrow critical-error subset, as a blocking gate."""
        test_job = self.workflow["jobs"]["test"]
        all_run_steps = " ".join(
            s.get("run", "") for s in test_job["steps"] if "run" in s
        )
        assert "ruff check ." in all_run_steps
        assert "--select E9,F63,F7,F82" not in all_run_steps

    def test_runs_ruff_format_check(self) -> None:
        test_job = self.workflow["jobs"]["test"]
        all_run_steps = " ".join(
            s.get("run", "") for s in test_job["steps"] if "run" in s
        )
        assert "ruff format --check ." in all_run_steps

    def test_runs_pytest(self) -> None:
        test_job = self.workflow["jobs"]["test"]
        all_run_steps = " ".join(
            s.get("run", "") for s in test_job["steps"] if "run" in s
        )
        assert "pytest" in all_run_steps

    def test_runs_pip_audit_dependency_scan(self) -> None:
        test_job = self.workflow["jobs"]["test"]
        all_run_steps = " ".join(
            s.get("run", "") for s in test_job["steps"] if "run" in s
        )
        assert "pip-audit" in all_run_steps

    def test_runs_gitleaks_secret_scan(self) -> None:
        test_job = self.workflow["jobs"]["test"]
        steps = test_job["steps"]
        gitleaks_step = next(
            (s for s in steps if "gitleaks" in s.get("uses", "")), None
        )
        assert gitleaks_step is not None


class TestCodeQLWorkflow:
    """Validate codeql.yml structure."""

    def setup_method(self) -> None:
        self.workflow = _load_workflow("codeql.yml")

    def test_triggers_on_pull_request_and_push(self) -> None:
        assert "pull_request" in self.workflow["on"]
        assert "push" in self.workflow["on"]

    def test_has_analyze_job(self) -> None:
        assert "analyze" in self.workflow["jobs"]

    def test_analyzes_python(self) -> None:
        analyze_job = self.workflow["jobs"]["analyze"]
        languages = analyze_job["strategy"]["matrix"]["language"]
        assert "python" in languages

    def test_uses_codeql_init_and_analyze_actions(self) -> None:
        analyze_job = self.workflow["jobs"]["analyze"]
        steps = analyze_job["steps"]
        uses_list = [s.get("uses", "") for s in steps]
        assert any("codeql-action/init" in u for u in uses_list)
        assert any("codeql-action/analyze" in u for u in uses_list)


class TestDeployWorkflow:
    """Validate deploy.yml structure."""

    def setup_method(self) -> None:
        self.workflow = _load_workflow("deploy.yml")

    def test_triggers_on_push_to_main(self) -> None:
        assert "push" in self.workflow["on"]
        assert "main" in self.workflow["on"]["push"]["branches"]

    def test_has_required_jobs(self) -> None:
        jobs = self.workflow["jobs"]
        for job_name in ("test", "migrate", "deploy-staging", "deploy-production"):
            assert job_name in jobs, f"Missing job: {job_name}"

    def test_job_dependency_chain(self) -> None:
        jobs = self.workflow["jobs"]
        assert "test" in jobs["migrate"]["needs"]
        assert "migrate" in jobs["deploy-staging"]["needs"]
        assert "deploy-staging" in jobs["deploy-production"]["needs"]

    def test_production_requires_environment_approval(self) -> None:
        prod_job = self.workflow["jobs"]["deploy-production"]
        assert prod_job["environment"] == "production"

    def test_staging_uses_environment(self) -> None:
        staging_job = self.workflow["jobs"]["deploy-staging"]
        assert staging_job["environment"] == "staging"

    def test_release_sha_in_env(self) -> None:
        assert "RELEASE_SHA" in self.workflow.get("env", {})

    def test_migration_runs_module(self) -> None:
        migrate_job = self.workflow["jobs"]["migrate"]
        all_run_steps = " ".join(
            s.get("run", "") for s in migrate_job["steps"] if "run" in s
        )
        assert "boardmatch.infrastructure.db.migrations" in all_run_steps
