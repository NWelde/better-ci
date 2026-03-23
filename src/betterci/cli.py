# cli.py
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import click

from betterci.runner import run_dag, load_workflow
from betterci.agent.executor import job_to_dict
from betterci.git_facts.git import get_remote_url, get_current_ref
from betterci.ui.console import Console, set_console, get_console


# ---------------------------------------------------------------------------
# Workflow discovery
# ---------------------------------------------------------------------------

def find_workflow_files() -> list[Path]:
    current_dir = Path(".")
    workflow_files = []
    default = current_dir / "betterci_workflow.py"
    if default.exists():
        workflow_files.append(default)
    for path in sorted(current_dir.glob("*_workflow.py")):
        if path != default:
            workflow_files.append(path)
    return workflow_files


def discover_workflow(workflow_arg: str | None) -> Path:
    console = get_console()

    if workflow_arg:
        p = Path(workflow_arg)
        if not p.exists() and p.suffix != ".py":
            p = Path(str(p) + ".py")
        if not p.exists():
            console.print_error(
                "Workflow file not found",
                f"Could not find: {workflow_arg}",
                suggestion="betterci run --workflow my_workflow.py",
            )
            sys.exit(1)
        return p

    files = find_workflow_files()

    if not files:
        console.print_error(
            "No workflow file found",
            "Could not find betterci_workflow.py or any *_workflow.py file.",
            suggestion=(
                "Create a workflow file, for example:\n\n"
                "  # betterci_workflow.py\n"
                "  from betterci import wf, job, sh\n\n"
                "  def workflow():\n"
                "      return wf(\n"
                "          job('lint', sh('ruff', 'ruff check src/')),\n"
                "          job('test', sh('pytest', 'pytest -q'), needs=['lint']),\n"
                "      )"
            ),
        )
        sys.exit(1)

    if len(files) > 1:
        file_list = "\n".join(f"  {f}" for f in files)
        console.print_error(
            "Multiple workflow files found",
            "Specify which one to use:",
            details=[file_list],
            suggestion="betterci run --workflow betterci_workflow.py",
        )
        sys.exit(1)

    return files[0]


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Show full stack traces and internal debug output.",
)
@click.pass_context
def cli(ctx, debug):
    """BetterCI — code-aware, deterministic CI runner."""
    console = Console(debug=debug)
    set_console(console)
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug


# ---------------------------------------------------------------------------
# betterci run
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--workflow",
    default=None,
    help="Workflow file (default: betterci_workflow.py).",
)
@click.option("--workers", default=None, type=int, help="Parallel job limit.")
@click.option("--cache-dir", default=".betterci/cache", show_default=True, help="Cache directory.")
@click.option(
    "--fail-fast/--no-fail-fast",
    default=True,
    show_default=True,
    help="Stop scheduling new jobs after the first failure.",
)
@click.option(
    "--git-diff/--no-git-diff",
    default=False,
    help="Select jobs based on which files changed in git.",
)
@click.option(
    "--compare-ref",
    default="origin/main",
    show_default=True,
    help="Git ref to diff against when using --git-diff.",
)
@click.option(
    "--print-plan/--no-print-plan",
    default=True,
    show_default=True,
    help="Print selected/skipped jobs before executing.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Stream step output in real-time instead of buffering.",
)
@click.option(
    "--safe",
    is_flag=True,
    default=False,
    help=(
        "Enforce the constrained execution model: fail if the workflow file "
        "imports anything outside of the betterci package."
    ),
)
@click.pass_context
def run(
    ctx,
    workflow,
    workers,
    cache_dir,
    fail_fast,
    git_diff,
    compare_ref,
    print_plan,
    verbose,
    safe,
):
    """Run a BetterCI workflow locally."""
    console = get_console()
    # Propagate verbose to console (used by runner for real-time streaming)
    console.verbose = verbose

    workflow_path = discover_workflow(workflow)

    try:
        try:
            repo_url = get_remote_url("origin")
            repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        except Exception:
            repo_name = Path(".").resolve().name

        jobs = load_workflow(workflow_path, safe=safe)

        console.print_run_started(
            repository=repo_name,
            workflow=workflow_path.name,
            job_count=len(jobs),
        )

        results = run_dag(
            jobs,
            repo_root=".",
            cache_root=cache_dir,
            max_workers=workers,
            fail_fast=fail_fast,
            use_git_diff=git_diff,
            compare_ref=compare_ref,
            print_plan=print_plan,
            verbose=verbose,
            safe=safe,
        )

        console.print_results(results)

        if any(v == "failed" for v in results.values()):
            sys.exit(1)

    except KeyboardInterrupt:
        console.print_info("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        console.print_exception(e)
        sys.exit(1)


# ---------------------------------------------------------------------------
# betterci agent
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--api", required=True, help="Cloud API base URL (e.g. http://localhost:8000).")
@click.option("--agent-id", default=None, help="Agent identifier (default: hostname).")
@click.option(
    "--poll-interval",
    default=5,
    type=int,
    show_default=True,
    help="Seconds between polls when no jobs are queued.",
)
@click.pass_context
def agent(ctx, api, agent_id, poll_interval):
    """Start a BetterCI worker agent."""
    import socket
    from betterci.agent.agent import run_agent

    console = get_console()

    if not agent_id:
        agent_id = socket.gethostname()

    try:
        run_agent(api, agent_id, poll_interval)
    except KeyboardInterrupt:
        console.print_info("\nAgent stopped.")
        sys.exit(0)
    except Exception as e:
        console.print_exception(e)
        sys.exit(1)


# ---------------------------------------------------------------------------
# betterci submit
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--api", required=True, help="Cloud API base URL.")
@click.option("--workflow", default=None, help="Workflow file.")
@click.option("--repo", default=None, help="Repository URL (default: git remote origin).")
@click.option("--ref", default=None, help="Git ref/branch/SHA (default: current HEAD).")
@click.option("--api-key", default=None, envvar="BETTERCI_API_KEY", help="API key for authentication.")
@click.pass_context
def submit(ctx, api, workflow, repo, ref, api_key):
    """Submit a workflow run to the BetterCI cloud API."""
    import subprocess

    console = get_console()
    workflow_path = discover_workflow(workflow)

    try:
        jobs = load_workflow(workflow_path)
        console.print_info(f"Loaded {len(jobs)} job(s) from {workflow_path.name}")
    except Exception as e:
        console.print_error("Failed to load workflow", str(e))
        sys.exit(1)

    if not repo:
        try:
            repo = get_remote_url("origin")
            console.print_debug(f"Repository URL: {repo}")
        except Exception as e:
            console.print_error(
                "Could not get repository URL",
                "No --repo specified and git remote origin is unavailable.",
                suggestion="betterci submit --api <url> --repo <repo_url>",
            )
            sys.exit(1)

    if not ref:
        try:
            ref = get_current_ref()
            console.print_debug(f"Git ref: {ref}")
        except Exception:
            console.print_error(
                "Could not determine git ref",
                "No --ref specified and could not read current git ref.",
                suggestion="betterci submit --api <url> --ref <branch>",
            )
            sys.exit(1)

    api_jobs = []
    for j in jobs:
        job_dict = job_to_dict(j)
        api_jobs.append({
            "job_name": j.name,
            "payload_json": {
                "repo_url": repo,
                "ref": ref,
                "job": job_dict,
            },
        })

    base_url = api.rstrip("/")
    url = urljoin(base_url + "/", "runs")
    body = json.dumps({"repo": repo, "jobs": api_jobs}).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            run_id = data.get("run_id")
            job_ids = data.get("job_ids", [])
            console.print_info(f"\nSubmitted to {base_url}")
            console.print_info(f"  Run ID  : {run_id}")
            console.print_info(f"  Jobs    : {', '.join(job_ids)}")
            console.print_info("\nMonitor with: betterci agent --api <url>")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        console.print_error(
            "API request failed",
            f"HTTP {e.code} {e.reason}",
            details=[body_text] if body_text else None,
        )
        sys.exit(1)
    except urllib.error.URLError as e:
        console.print_error(
            "Network error",
            f"Could not connect to {base_url}: {e.reason}",
            suggestion="Check the API URL and verify the server is running.",
        )
        sys.exit(1)
    except Exception as e:
        console.print_exception(e)
        sys.exit(1)


if __name__ == "__main__":
    cli()
