"""Tests for betterci.runner — preflight, step expansion, DAG, selection."""
import os
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from betterci.model import Job, Step
import betterci.dsl as dsl
from betterci.dsl import job, sh, lint_step
dsl_test = dsl.test
make_test_step = dsl_test  # alias used in TestExpandSteps
from betterci.runner import (
    _preflight_tools,
    _preflight_secrets,
    _run_preflight,
    _expand_steps,
    _build_graph,
    _matches_any,
    load_workflow,
    run_dag,
    CIError,
    StepFailure,
)
from betterci.ui.console import set_console, Console

set_console(Console())  # silence colors in test output


# ---------------------------------------------------------------------------
# Pre-flight: tool checks
# ---------------------------------------------------------------------------

class TestPreflightTools:
    def test_passes_when_tool_exists(self):
        # python3 is always available in the test environment
        j = job("x", sh("r", "echo"), requires=["python3"])
        missing = _preflight_tools(j)
        assert missing == []

    def test_catches_missing_tool(self):
        j = job("x", sh("r", "echo"), requires=["__nonexistent_tool_xyz__"])
        missing = _preflight_tools(j)
        assert "__nonexistent_tool_xyz__" in missing

    def test_empty_requires(self):
        j = job("x", sh("r", "echo"))
        assert _preflight_tools(j) == []

    def test_multiple_tools(self):
        j = job("x", sh("r", "echo"),
                requires=["python3", "__missing_1__", "__missing_2__"])
        missing = _preflight_tools(j)
        assert "__missing_1__" in missing
        assert "__missing_2__" in missing
        assert "python3" not in missing


# ---------------------------------------------------------------------------
# Pre-flight: secret checks
# ---------------------------------------------------------------------------

class TestPreflightSecrets:
    def test_passes_when_set_in_env(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "value")
        j = job("x", sh("r", "echo"), secrets=["MY_SECRET"])
        assert _preflight_secrets(j) == []

    def test_passes_when_set_in_job_env(self):
        j = job("x", sh("r", "echo"), secrets=["MY_SECRET"], env={"MY_SECRET": "val"})
        assert _preflight_secrets(j) == []

    def test_catches_missing_secret(self, monkeypatch):
        monkeypatch.delenv("__BETTERCI_TEST_SECRET__", raising=False)
        j = job("x", sh("r", "echo"), secrets=["__BETTERCI_TEST_SECRET__"])
        missing = _preflight_secrets(j)
        assert "__BETTERCI_TEST_SECRET__" in missing

    def test_empty_secrets(self):
        j = job("x", sh("r", "echo"))
        assert _preflight_secrets(j) == []


class TestRunPreflight:
    def test_raises_ci_error_on_missing_tool(self):
        j = job("x", sh("r", "echo"), requires=["__bad_tool__"])
        with pytest.raises(CIError) as exc:
            _run_preflight(j)
        assert exc.value.kind == "missing_tools"

    def test_raises_ci_error_on_missing_secret(self, monkeypatch):
        monkeypatch.delenv("__SECRET_XYZ__", raising=False)
        j = job("x", sh("r", "echo"), secrets=["__SECRET_XYZ__"])
        with pytest.raises(CIError) as exc:
            _run_preflight(j)
        assert exc.value.kind == "missing_secrets"

    def test_passes_when_all_ok(self, monkeypatch):
        monkeypatch.setenv("TEST_TOKEN", "abc")
        j = job("x", sh("r", "echo"), requires=["python3"], secrets=["TEST_TOKEN"])
        _run_preflight(j)  # should not raise


# ---------------------------------------------------------------------------
# Step expansion
# ---------------------------------------------------------------------------

class TestExpandSteps:
    def test_regular_steps_pass_through(self):
        j = job("x", sh("a", "echo a"), sh("b", "echo b"))
        expanded = _expand_steps(j)
        assert len(expanded) == 2
        assert expanded[0].run == "echo a"

    def test_make_test_step_expands(self):
        j = job("test", make_test_step("Run pytest", framework="pytest", args="-q"))
        expanded = _expand_steps(j)
        assert len(expanded) == 2
        assert any("pytest" in s.run for s in expanded)

    def test_make_test_step_no_install(self):
        j = job("test", make_test_step("Run", framework="pytest", install=False))
        expanded = _expand_steps(j)
        assert len(expanded) == 1
        assert "pytest" in expanded[0].run

    def test_npm_make_test_step_expands(self):
        j = job("test", make_test_step("JS", framework="npm", install=True))
        expanded = _expand_steps(j)
        assert len(expanded) == 2
        assert any("npm ci" in s.run for s in expanded)
        assert any("npm test" in s.run for s in expanded)

    def test_mixed_steps(self):
        j = job("x",
                sh("setup", "echo setup"),
                make_test_step("tests", framework="pytest", install=False),
                sh("teardown", "echo done"))
        expanded = _expand_steps(j)
        # setup + 1 pytest (no install) + teardown = 3
        assert len(expanded) == 3

    def test_workflow_type_steps_pass_through(self):
        """lint_step() and docker_step() should not be expanded."""
        j = job("lint", lint_step("Ruff", "ruff", "check src/"))
        expanded = _expand_steps(j)
        assert len(expanded) == 1
        assert expanded[0].workflow_type == "lint"

    def test_unknown_framework_raises(self):
        bad = Step(name="x", kind="test", data={"framework": "cargo"})
        j = Job(name="x", steps=[bad])
        with pytest.raises(ValueError, match="Unknown test framework"):
            _expand_steps(j)


# ---------------------------------------------------------------------------
# DAG construction
# ---------------------------------------------------------------------------

class TestBuildGraph:
    def test_simple_chain(self):
        jobs = [
            job("lint", sh("r", "echo")),
            job("test", sh("r", "echo"), needs=["lint"]),
        ]
        by_name, adj, indeg = _build_graph(jobs)
        assert indeg["lint"] == 0
        assert indeg["test"] == 1
        assert "test" in adj["lint"]

    def test_independent_jobs_zero_indegree(self):
        jobs = [job("a", sh("r", "echo")), job("b", sh("r", "echo"))]
        _, _, indeg = _build_graph(jobs)
        assert indeg["a"] == 0
        assert indeg["b"] == 0

    def test_duplicate_name_raises(self):
        jobs = [job("a", sh("r", "echo")), job("a", sh("r", "echo"))]
        with pytest.raises(ValueError, match="Duplicate job name"):
            _build_graph(jobs)

    def test_missing_dependency_raises(self):
        jobs = [job("test", sh("r", "echo"), needs=["lint"])]
        with pytest.raises(ValueError, match="does not exist"):
            _build_graph(jobs)

    def test_diamond_dependency(self):
        jobs = [
            job("base",   sh("r", "echo")),
            job("left",   sh("r", "echo"), needs=["base"]),
            job("right",  sh("r", "echo"), needs=["base"]),
            job("top",    sh("r", "echo"), needs=["left", "right"]),
        ]
        _, _, indeg = _build_graph(jobs)
        assert indeg["base"] == 0
        assert indeg["top"] == 2


# ---------------------------------------------------------------------------
# Job selection (git-diff)
# ---------------------------------------------------------------------------

class TestMatchesAny:
    def test_exact(self):
        assert _matches_any("src/main.py", ["src/main.py"])

    def test_glob_star(self):
        assert _matches_any("src/utils.py", ["src/*.py"])

    def test_glob_double_star(self):
        assert _matches_any("src/deep/nested/file.py", ["src/**"])

    def test_no_match(self):
        assert not _matches_any("README.md", ["src/**"])


# ---------------------------------------------------------------------------
# load_workflow + constrained execution model
# ---------------------------------------------------------------------------

class TestLoadWorkflow:
    def _write_wf(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "test_workflow.py"
        p.write_text(textwrap.dedent(content))
        return p

    def test_loads_workflow_function(self, tmp_path):
        p = self._write_wf(tmp_path, """
            from betterci import wf, job, sh
            def workflow():
                return wf(job("lint", sh("run", "ruff check src/")))
        """)
        jobs = load_workflow(p)
        assert len(jobs) == 1
        assert jobs[0].name == "lint"

    def test_loads_jobs_list(self, tmp_path):
        p = self._write_wf(tmp_path, """
            from betterci import job, sh
            JOBS = [job("test", sh("run", "pytest"))]
        """)
        jobs = load_workflow(p)
        assert jobs[0].name == "test"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_workflow(tmp_path / "nonexistent.py")

    def test_non_py_raises(self, tmp_path):
        p = tmp_path / "wf.yaml"
        p.write_text("jobs:")
        with pytest.raises(ValueError, match=".py"):
            load_workflow(p)

    def test_safe_mode_rejects_external_imports(self, tmp_path):
        p = self._write_wf(tmp_path, """
            import os
            from betterci import wf, job, sh
            def workflow():
                return wf(job("x", sh("r", "echo")))
        """)
        with pytest.raises(CIError) as exc:
            load_workflow(p, safe=True)
        assert exc.value.kind == "unsafe_workflow"

    def test_safe_mode_allows_betterci_imports(self, tmp_path):
        p = self._write_wf(tmp_path, """
            from betterci import wf, job, sh
            from betterci.dsl import matrix
            def workflow():
                return wf(job("x", sh("r", "echo")))
        """)
        jobs = load_workflow(p, safe=True)
        assert len(jobs) == 1

    def test_non_safe_warns_but_loads(self, tmp_path):
        """External imports trigger a warning but don't block loading in normal mode."""
        p = self._write_wf(tmp_path, """
            import os
            from betterci import wf, job, sh
            def workflow():
                return wf(job("x", sh("r", "echo")))
        """)
        # Should load without raising
        jobs = load_workflow(p, safe=False)
        assert len(jobs) == 1


# ---------------------------------------------------------------------------
# run_dag — end-to-end with real subprocess
# ---------------------------------------------------------------------------

class TestRunDag:
    def test_single_job_success(self, tmp_path):
        jobs = [job("greet", sh("say hi", "echo hello"))]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False)
        assert results == {"greet": "ok"}

    def test_chained_jobs(self, tmp_path):
        jobs = [
            job("a", sh("step", "echo a")),
            job("b", sh("step", "echo b"), needs=["a"]),
        ]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False)
        assert results == {"a": "ok", "b": "ok"}

    def test_failed_job(self, tmp_path):
        jobs = [job("fail", sh("bad", "exit 1"))]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, fail_fast=False)
        assert results["fail"] == "failed"

    def test_fail_fast_skips_downstream(self, tmp_path):
        jobs = [
            job("fail", sh("bad", "exit 1")),
            job("downstream", sh("ok", "echo ok"), needs=["fail"]),
        ]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, fail_fast=True)
        assert results["fail"] == "failed"
        # downstream was never scheduled
        assert "downstream" not in results

    def test_parallel_independent_jobs(self, tmp_path):
        jobs = [
            job("a", sh("step", "echo a")),
            job("b", sh("step", "echo b")),
            job("c", sh("step", "echo c")),
        ]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, max_workers=3)
        assert all(v == "ok" for v in results.values())

    def test_secrets_fail_fast_before_steps(self, tmp_path, monkeypatch):
        """Missing secrets should fail the job before any step runs."""
        monkeypatch.delenv("__MISSING_SECRET__", raising=False)
        ran = []
        j = job("deploy", sh("push", "echo pushed"), secrets=["__MISSING_SECRET__"])
        results = run_dag([j], repo_root=tmp_path, print_plan=False, fail_fast=False)
        assert results["deploy"] == "failed"

    def test_cwd_respected(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "marker.txt").write_text("found")
        jobs = [job("check", sh("look", "test -f marker.txt", cwd="sub"))]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False)
        assert results["check"] == "ok"
