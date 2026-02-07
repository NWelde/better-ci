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


@click.group()
def cli():
    """BetterCI — deterministic, cache-aware CI runner."""
    pass


@cli.command()
@click.option(
    "--workflow",
    default="betterci_workflow",
    help="Workflow name or path (e.g. betterci_workflow or betterci_workflow.py)",
)
@click.option("--workers", default=None, type=int, help="Number of parallel workers")
@click.option("--cache-dir", default=".betterci/cache", help="Cache directory")
@click.option("--fail-fast/--no-fail-fast", default=True, help="Stop scheduling new jobs after first failure")
@click.option("--git-diff/--no-git-diff", default=False, help="Select jobs based on git diff and job.paths")
@click.option("--compare-ref", default="origin/main", show_default=True, help="Git ref to diff against")
@click.option("--print-plan/--no-print-plan", default=True, show_default=True, help="Print selected/skipped jobs")
def run(workflow, workers, cache_dir, fail_fast, git_diff, compare_ref, print_plan):
    """Run a BetterCI workflow."""

    workflow_path = Path(workflow)
    if not workflow_path.exists() and workflow_path.suffix != ".py":
        workflow_path = Path(str(workflow_path) + ".py")
    if not workflow_path.exists():
        click.echo(f"Workflow file not found: {workflow}")
        sys.exit(1)

    try:
        jobs = load_workflow(workflow_path)
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

        click.echo("\nResults:")
        for job, status in results.items():
            click.echo(f"  {job}: {status}")

        if any(v == "failed" for v in results.values()):
            sys.exit(1)

    except Exception as e:
        click.echo(f"Error: {e}")
        sys.exit(1)


@cli.command()
@click.option("--api", required=True, help="API base URL (e.g., http://localhost:8000)")
@click.option("--agent-id", default=None, help="Unique agent identifier (defaults to hostname)")
@click.option("--poll-interval", default=5, type=int, help="Polling interval in seconds when no jobs available")
def agent(api, agent_id, poll_interval):
    """Run BetterCI agent loop to poll for and execute jobs."""
    import socket
    from betterci.agent.agent import run_agent
    
    # Generate agent_id if not provided
    if not agent_id:
        agent_id = socket.gethostname()
    
    try:
        run_agent(api, agent_id, poll_interval)
    except KeyboardInterrupt:
        click.echo("\nAgent stopped by user")
        sys.exit(0)
    except Exception as e:
        click.echo(f"Error: {e}")
        sys.exit(1)


@cli.command()
@click.option("--api", required=True, help="API base URL (e.g., http://localhost:8000)")
@click.option(
    "--workflow",
    default="betterci_workflow",
    help="Workflow name or path (e.g. betterci_workflow or betterci_workflow.py)",
)
@click.option("--repo", default=None, help="Repository URL (defaults to git remote origin URL)")
@click.option("--ref", default=None, help="Git ref/branch/commit (defaults to current branch or HEAD)")
def submit(api, workflow, repo, ref):
    """Submit a BetterCI workflow run to the cloud API."""
    import subprocess
    
    # Load workflow
    workflow_path = Path(workflow)
    if not workflow_path.exists() and workflow_path.suffix != ".py":
        workflow_path = Path(str(workflow_path) + ".py")
    if not workflow_path.exists():
        click.echo(f"Error: Workflow file not found: {workflow}", err=True)
        sys.exit(1)
    
    try:
        jobs = load_workflow(workflow_path)
        click.echo(f"Loaded {len(jobs)} job(s) from {workflow_path}")
    except Exception as e:
        click.echo(f"Error loading workflow: {e}", err=True)
        sys.exit(1)
    
    # Get repository URL
    if not repo:
        try:
            repo = get_remote_url("origin")
            click.echo(f"Using repository URL from git remote: {repo}")
        except subprocess.CalledProcessError:
            click.echo("Error: No --repo specified and could not get git remote URL. Please specify --repo.", err=True)
            sys.exit(1)
        except FileNotFoundError:
            click.echo("Error: git command not found. Please install Git or specify --repo.", err=True)
            sys.exit(1)
    
    # Get git ref
    if not ref:
        try:
            ref = get_current_ref()
            click.echo(f"Using git ref: {ref}")
        except subprocess.CalledProcessError:
            click.echo("Error: Could not determine git ref. Please specify --ref.", err=True)
            sys.exit(1)
        except FileNotFoundError:
            click.echo("Error: git command not found. Please specify --ref.", err=True)
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
                
                click.echo(f"\n✓ Successfully submitted run to {base_url}")
                click.echo(f"  Run ID: {run_id}")
                click.echo(f"  Job IDs: {', '.join(job_ids)}")
                click.echo(f"\nMonitor progress by running agents or checking the API.")
            else:
                click.echo("Error: Empty response from API", err=True)
                sys.exit(1)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        click.echo(f"Error: API request failed: {e.code} {e.reason}", err=True)
        if error_body:
            click.echo(f"  {error_body}", err=True)
        sys.exit(1)
    except urllib.error.URLError as e:
        click.echo(f"Error: Network error: {e.reason}", err=True)
        click.echo(f"  Could not connect to {base_url}. Is the API running?", err=True)
        sys.exit(1)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON response from API: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
