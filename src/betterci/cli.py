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


def find_workflow_files() -> list[Path]:
    """
    Find all workflow files in the current directory.
    
    Returns:
        List of Path objects for workflow files
    """
    workflow_files = []
    current_dir = Path(".")
    
    # Look for betterci_workflow.py
    default_workflow = current_dir / "betterci_workflow.py"
    if default_workflow.exists():
        workflow_files.append(default_workflow)
    
    # Look for other *_workflow.py files
    for path in current_dir.glob("*_workflow.py"):
        if path != default_workflow:
            workflow_files.append(path)
    
    return sorted(workflow_files)


def discover_workflow(workflow_arg: str | None) -> Path:
    """
    Discover workflow file from argument or default.
    
    Args:
        workflow_arg: Optional workflow argument from CLI
        
    Returns:
        Path to workflow file
        
    Raises:
        SystemExit: If workflow cannot be found or multiple workflows exist
    """
    console = get_console()
    
    # If workflow is explicitly provided, use it
    if workflow_arg:
        workflow_path = Path(workflow_arg)
        if not workflow_path.exists() and workflow_path.suffix != ".py":
            workflow_path = Path(str(workflow_path) + ".py")
        if not workflow_path.exists():
            console.print_error(
                "Workflow file not found",
                f"Could not find workflow file: {workflow_arg}",
                suggestion=f"Create a workflow file or specify a different path:\n  betterci run --workflow my_workflow.py",
            )
            sys.exit(1)
        return workflow_path
    
    # Otherwise, try to discover workflow
    workflow_files = find_workflow_files()
    
    if len(workflow_files) == 0:
        console.print_error(
            "No workflow file found",
            "Could not find any workflow files.",
            details=[
                "Looked for:",
                "  betterci_workflow.py",
                "  *_workflow.py",
            ],
            suggestion="Create a workflow file:\n  betterci_workflow.py\n\nOr specify a workflow explicitly:\n  betterci run --workflow my_workflow.py",
        )
        sys.exit(1)
    
    if len(workflow_files) > 1:
        file_list = "\n".join(f"  {f}" for f in workflow_files)
        console.print_error(
            "Multiple workflow files found",
            "Found multiple workflow files. Please specify which one to use:",
            details=[file_list],
            suggestion="Specify a workflow explicitly:\n  betterci run --workflow betterci_workflow.py",
        )
        sys.exit(1)
    
    return workflow_files[0]


@click.group()
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug mode (show stack traces and detailed output)",
)
@click.pass_context
def cli(ctx, debug):
    """BetterCI — deterministic, cache-aware CI runner."""
    # Initialize console with debug flag
    console = Console(debug=debug)
    set_console(console)
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug


@cli.command()
@click.option(
    "--workflow",
    default=None,
    help="Workflow file path (defaults to betterci_workflow.py if present)",
)
@click.option("--workers", default=None, type=int, help="Number of parallel workers")
@click.option("--cache-dir", default=".betterci/cache", help="Cache directory")
@click.option("--fail-fast/--no-fail-fast", default=True, help="Stop scheduling new jobs after first failure")
@click.option("--git-diff/--no-git-diff", default=False, help="Select jobs based on git diff and job.paths")
@click.option("--compare-ref", default="origin/main", show_default=True, help="Git ref to diff against")
@click.option("--print-plan/--no-print-plan", default=True, show_default=True, help="Print selected/skipped jobs")
@click.pass_context
def run(ctx, workflow, workers, cache_dir, fail_fast, git_diff, compare_ref, print_plan):
    """Run a BetterCI workflow."""
    console = get_console()
    
    # Discover workflow file
    workflow_path = discover_workflow(workflow)
    
    try:
        # Get repository info for header
        try:
            repo_url = get_remote_url("origin")
            repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        except Exception:
            repo_name = Path(".").resolve().name
        
        # Load workflow
        jobs = load_workflow(workflow_path)
        
        # Print run header
        console.print_run_started(
            repository=repo_name,
            workflow=workflow_path.name,
            job_count=len(jobs),
        )
        
        # Run DAG
        results = run_dag(
            jobs,
            repo_root=".",
            cache_root=cache_dir,
            max_workers=workers,
            fail_fast=fail_fast,
            use_git_diff=git_diff,
            compare_ref=compare_ref,
            print_plan=print_plan,
        )
        
        # Print results
        console.print_results(results)
        
        if any(v == "failed" for v in results.values()):
            sys.exit(1)
    
    except KeyboardInterrupt:
        console.print_info("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        console.print_exception(e)
        if ctx.obj.get("debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.option("--api", required=True, help="API base URL (e.g., http://localhost:8000)")
@click.option("--agent-id", default=None, help="Unique agent identifier (defaults to hostname)")
@click.option("--poll-interval", default=5, type=int, help="Polling interval in seconds when no jobs available")
@click.pass_context
def agent(ctx, api, agent_id, poll_interval):
    """Run BetterCI agent loop to poll for and execute jobs."""
    import socket
    from betterci.agent.agent import run_agent
    
    console = get_console()
    
    # Generate agent_id if not provided
    if not agent_id:
        agent_id = socket.gethostname()
    
    try:
        run_agent(api, agent_id, poll_interval)
    except KeyboardInterrupt:
        console.print_info("\nAgent stopped by user")
        sys.exit(0)
    except Exception as e:
        console.print_exception(e)
        if ctx.obj.get("debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.option("--api", required=True, help="API base URL (e.g., http://localhost:8000)")
@click.option(
    "--workflow",
    default=None,
    help="Workflow file path (defaults to betterci_workflow.py if present)",
)
@click.option("--repo", default=None, help="Repository URL (defaults to git remote origin URL)")
@click.option("--ref", default=None, help="Git ref/branch/commit (defaults to current branch or HEAD)")
@click.pass_context
def submit(ctx, api, workflow, repo, ref):
    """Submit a BetterCI workflow run to the cloud API."""
    import subprocess
    
    console = get_console()
    
    # Discover workflow file
    workflow_path = discover_workflow(workflow)
    
    try:
        jobs = load_workflow(workflow_path)
        console.print_info(f"Loaded {len(jobs)} job(s) from {workflow_path}")
    except Exception as e:
        console.print_error(
            "Failed to load workflow",
            f"Could not load workflow from {workflow_path}",
            details=[str(e)],
        )
        if ctx.obj.get("debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)
    
    # Get repository URL
    if not repo:
        try:
            repo = get_remote_url("origin")
            console.print_debug(f"Using repository URL from git remote: {repo}")
        except subprocess.CalledProcessError:
            console.print_error(
                "Could not get repository URL",
                "No --repo specified and could not get git remote URL.",
                suggestion="Please specify --repo explicitly:\n  betterci submit --api <url> --repo <repo_url>",
            )
            sys.exit(1)
        except FileNotFoundError:
            console.print_error(
                "Git command not found",
                "Could not find git command.",
                suggestion="Install Git or specify --repo explicitly:\n  betterci submit --api <url> --repo <repo_url>",
            )
            sys.exit(1)
    
    # Get git ref
    if not ref:
        try:
            ref = get_current_ref()
            console.print_debug(f"Using git ref: {ref}")
        except subprocess.CalledProcessError:
            console.print_error(
                "Could not determine git ref",
                "Could not get current git ref.",
                suggestion="Please specify --ref explicitly:\n  betterci submit --api <url> --ref <ref>",
            )
            sys.exit(1)
        except FileNotFoundError:
            console.print_error(
                "Git command not found",
                "Could not find git command.",
                suggestion="Install Git or specify --ref explicitly:\n  betterci submit --api <url> --ref <ref>",
            )
            sys.exit(1)
    
    # Serialize jobs and prepare API request
    api_jobs = []
    for job in jobs:
        job_dict = job_to_dict(job)
        payload_json = {
            "repo_url": repo,
            "ref": ref,
            "job": job_dict,
        }
        api_jobs.append({
            "job_name": job.name,
            "payload_json": payload_json,
        })
    
    # Submit to API
    base_url = api.rstrip("/")
    url = urljoin(base_url + "/", "runs")
    
    request_data = {
        "repo": repo,
        "jobs": api_jobs,
    }
    
    req_headers = {
        "Content-Type": "application/json",
    }
    req_data = json.dumps(request_data).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers=req_headers, method="POST")
    
    try:
        with urllib.request.urlopen(req) as response:
            response_data = response.read().decode("utf-8")
            if response_data:
                result = json.loads(response_data)
                run_id = result.get("run_id")
                job_ids = result.get("job_ids", [])
                
                console.print_info(f"\nSuccessfully submitted run to {base_url}")
                console.print_info(f"  Run ID: {run_id}")
                console.print_info(f"  Job IDs: {', '.join(job_ids)}")
                console.print_info(f"\nMonitor progress by running agents or checking the API.")
            else:
                console.print_error(
                    "Empty API response",
                    "Received empty response from API.",
                    suggestion=f"Check if the API at {base_url} is running correctly.",
                )
                sys.exit(1)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        console.print_error(
            "API request failed",
            f"HTTP {e.code} {e.reason}",
            details=[error_body] if error_body else None,
            suggestion=f"Check the API at {base_url} and verify your request.",
        )
        if ctx.obj.get("debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)
    except urllib.error.URLError as e:
        console.print_error(
            "Network error",
            f"Could not connect to {base_url}",
            details=[str(e.reason)],
            suggestion="Verify the API URL is correct and the API is running.",
        )
        if ctx.obj.get("debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)
    except json.JSONDecodeError as e:
        console.print_error(
            "Invalid API response",
            "Could not parse JSON response from API.",
            details=[str(e)],
            suggestion=f"Check if the API at {base_url} is responding correctly.",
        )
        if ctx.obj.get("debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        console.print_exception(e)
        if ctx.obj.get("debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    cli()
