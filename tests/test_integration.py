"""
Integration tests — run real workflows against the example project.

These tests actually execute shell commands (echo, python3, etc.) and verify
end-to-end behaviour: loading, preflight, step execution, caching, and DAG order.

They are slower than unit tests but exercise the full pipeline.
"""
import os
import sys
import textwrap
from pathlib import Path

import pytest

from betterci.dsl import job, sh, wf
from betterci.runner import load_workflow, run_dag, CIError
from betterci.ui.console import set_console, Console

set_console(Console())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wf(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "betterci_workflow.py"
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# Full pipeline: load → select → execute
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_simple_two_job_chain(self, tmp_path):
        p = _write_wf(tmp_path, """
            from betterci import wf, job, sh
            def workflow():
                return wf(
                    job("lint",  sh("echo", "echo lint ok")),
                    job("test",  sh("echo", "echo test ok"), needs=["lint"]),
                )
        """)
        jobs = load_workflow(p)
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False)
        assert results == {"lint": "ok", "test": "ok"}

    def test_job_writes_file_and_downstream_reads_it(self, tmp_path):
        """Verify DAG ordering: downstream job sees file created by upstream."""
        p = _write_wf(tmp_path, """
            from betterci import wf, job, sh
            def workflow():
                return wf(
                    job("produce", sh("create", "echo artifact > artifact.txt")),
                    job("consume", sh("read",   "cat artifact.txt"), needs=["produce"]),
                )
        """)
        jobs = load_workflow(p)
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False)
        assert results == {"produce": "ok", "consume": "ok"}

    def test_env_vars_passed_to_steps(self, tmp_path):
        # Write the check script separately to avoid quoting hell in the workflow string
        check_script = tmp_path / "check_env.py"
        check_script.write_text(
            "import os, sys\n"
            "assert os.environ.get('GREETING') == 'hello', "
            "f\"GREETING={os.environ.get('GREETING')!r}\"\n"
        )
        p = _write_wf(tmp_path, f"""
            from betterci import wf, job, sh
            def workflow():
                return wf(
                    job("check-env",
                        sh("verify", "python3 {check_script}"),
                        env={{"GREETING": "hello"}},
                    ),
                )
        """)
        jobs = load_workflow(p)
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False)
        assert results["check-env"] == "ok"

    def test_failed_step_marks_job_failed(self, tmp_path):
        jobs = [job("bad", sh("fail", "exit 42"))]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, fail_fast=False)
        assert results["bad"] == "failed"

    def test_secrets_validated_before_any_step_runs(self, tmp_path, monkeypatch):
        monkeypatch.delenv("__NO_SUCH_SECRET__", raising=False)
        # The step would succeed if it ran — but secrets check should block it.
        jobs = [job("deploy",
                    sh("push", "echo pushed"),
                    secrets=["__NO_SUCH_SECRET__"])]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, fail_fast=False)
        assert results["deploy"] == "failed"

    def test_requires_tool_validated_before_steps(self, tmp_path):
        jobs = [job("x", sh("ok", "echo hi"), requires=["__no_such_tool_xyz__"])]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, fail_fast=False)
        assert results["x"] == "failed"

    def test_caching_skips_on_hit(self, tmp_path):
        counter_file = tmp_path / "ran_count.txt"
        counter_file.write_text("0")

        # Build directory to cache
        cached = tmp_path / "cached_output"
        cached.mkdir()
        (cached / "result.txt").write_text("built")

        jobs = [job(
            "build",
            sh("count", f"echo $(($(cat {counter_file}) + 1)) > {counter_file}"),
            cache_dirs=["cached_output"],
            cache_skip_on_hit=True,
        )]

        cache_dir = tmp_path / "cache"

        # First run: cache miss → step executes
        results1 = run_dag(jobs, repo_root=tmp_path, cache_root=cache_dir, print_plan=False)
        assert results1["build"] == "ok"

        # Second run: cache hit → step should be skipped
        results2 = run_dag(jobs, repo_root=tmp_path, cache_root=cache_dir, print_plan=False)
        assert results2["build"] == "skipped(cache)"

    def test_matrix_jobs_all_run(self, tmp_path):
        p = _write_wf(tmp_path, """
            from betterci import wf, job, sh, matrix
            def workflow():
                return wf(*matrix("ver", ["a", "b", "c"]).jobs(
                    lambda v: job(f"test-{v}", sh("echo", f"echo {v}"))
                ))
        """)
        jobs = load_workflow(p)
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, max_workers=3)
        assert set(results.keys()) == {"test-a", "test-b", "test-c"}
        assert all(v == "ok" for v in results.values())

    def test_safe_mode_blocks_unsafe_workflow(self, tmp_path):
        p = _write_wf(tmp_path, """
            import subprocess
            from betterci import wf, job, sh
            def workflow():
                return wf(job("x", sh("r", "echo")))
        """)
        with pytest.raises(CIError) as exc:
            load_workflow(p, safe=True)
        assert exc.value.kind == "unsafe_workflow"

    def test_cwd_missing_raises_helpful_error(self, tmp_path):
        jobs = [job("x", sh("run", "echo hi", cwd="nonexistent/subdir"))]
        results = run_dag(jobs, repo_root=tmp_path, print_plan=False, fail_fast=False)
        assert results["x"] == "failed"


# ---------------------------------------------------------------------------
# Integration against the example project
# ---------------------------------------------------------------------------

EXAMPLE_DIR = Path(__file__).parent.parent / "examples" / "my-app"


@pytest.mark.skipif(
    not EXAMPLE_DIR.exists(),
    reason="examples/my-app not found",
)
class TestExampleProject:
    def test_example_workflow_loads(self):
        jobs = load_workflow(EXAMPLE_DIR / "betterci_workflow.py")
        assert len(jobs) > 0
        job_names = {j.name for j in jobs}
        # The example should have at least lint and test jobs
        assert job_names & {"lint", "test"}, f"Expected lint/test jobs, got: {job_names}"

    def test_example_lint_and_test_run(self):
        """
        Run the lint and test jobs from the example project together
        (test depends on lint, so they must be submitted as a pair).
        """
        jobs = load_workflow(EXAMPLE_DIR / "betterci_workflow.py")
        # Keep only lint + test (skip build which needs python -m build)
        subset = [j for j in jobs if j.name in ("lint", "test")]
        if not subset:
            pytest.skip("No lint/test jobs in example workflow")

        results = run_dag(
            subset,
            repo_root=EXAMPLE_DIR,
            print_plan=False,
            fail_fast=True,
        )
        assert results.get("lint") == "ok",  f"Lint failed: {results}"
        assert results.get("test") == "ok",  f"Test failed: {results}"
