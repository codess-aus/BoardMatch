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

    def test_checkout_uses_full_history_for_gitleaks(self) -> None:
        """gitleaks-action needs the full commit history to diff base...head
        for pull_request events; a shallow (default) checkout causes it to
        fail with an ambiguous revision range error."""
        test_job = self.workflow["jobs"]["test"]
        checkout_step = next(
            (
                s
                for s in test_job["steps"]
                if s.get("uses", "").startswith("actions/checkout")
            ),
            None,
        )
        assert checkout_step is not None
        assert checkout_step.get("with", {}).get("fetch-depth") == 0

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
        for job_name in (
            "test",
            "build-and-push",
            "migrate-staging",
            "deploy-staging",
            "migrate-production",
            "deploy-production",
        ):
            assert job_name in jobs, f"Missing job: {job_name}"

    def test_job_dependency_chain(self) -> None:
        jobs = self.workflow["jobs"]
        assert "test" in jobs["build-and-push"]["needs"]
        assert "build-and-push" in jobs["migrate-staging"]["needs"]
        assert "migrate-staging" in jobs["deploy-staging"]["needs"]
        assert "deploy-staging" in jobs["migrate-production"]["needs"]
        assert "migrate-production" in jobs["deploy-production"]["needs"]

    def test_production_requires_environment_approval(self) -> None:
        prod_job = self.workflow["jobs"]["deploy-production"]
        assert prod_job["environment"] == "production"
        migrate_prod_job = self.workflow["jobs"]["migrate-production"]
        assert migrate_prod_job["environment"] == "production"

    def test_staging_uses_environment(self) -> None:
        staging_job = self.workflow["jobs"]["deploy-staging"]
        assert staging_job["environment"] == "staging"
        migrate_staging_job = self.workflow["jobs"]["migrate-staging"]
        assert migrate_staging_job["environment"] == "staging"

    def test_release_sha_in_env(self) -> None:
        assert "RELEASE_SHA" in self.workflow.get("env", {})

    def test_migration_runs_module(self) -> None:
        for job_name in ("migrate-staging", "migrate-production"):
            migrate_job = self.workflow["jobs"][job_name]
            all_run_steps = " ".join(
                s.get("run", "") for s in migrate_job["steps"] if "run" in s
            )
            assert "boardmatch.infrastructure.db.migrations" in all_run_steps

    def test_migration_database_url_from_secrets(self) -> None:
        """DATABASE_URL must come from environment secrets, never hardcoded."""
        for job_name in ("migrate-staging", "migrate-production"):
            migrate_job = self.workflow["jobs"][job_name]
            database_url = migrate_job.get("env", {}).get("DATABASE_URL", "")
            assert "secrets.DATABASE_URL" in database_url

    def test_build_and_push_uses_azure_login_and_acr(self) -> None:
        build_job = self.workflow["jobs"]["build-and-push"]
        steps = build_job["steps"]
        uses_list = [s.get("uses", "") for s in steps]
        assert any("azure/login" in u for u in uses_list)
        all_run_steps = " ".join(s.get("run", "") for s in steps if "run" in s)
        assert "az acr login" in all_run_steps
        assert "docker build" in all_run_steps
        assert "docker push" in all_run_steps

    def test_deploy_jobs_run_smoke_test_and_rollback(self) -> None:
        for job_name in ("deploy-staging", "deploy-production"):
            deploy_job = self.workflow["jobs"][job_name]
            steps = deploy_job["steps"]
            all_run_steps = " ".join(s.get("run", "") for s in steps if "run" in s)
            assert "health/ready" in all_run_steps
            step_ids = [s.get("id") for s in steps]
            assert "smoke_test" in step_ids
            rollback_step = next(
                (s for s in steps if "Roll back" in s.get("name", "")), None
            )
            assert rollback_step is not None
            assert "smoke_test" in rollback_step.get("if", "")
            assert "az containerapp update" in rollback_step.get("run", "")
