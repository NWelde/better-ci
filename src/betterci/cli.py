# cli.py
from __future__ import annotations

import sys
from pathlib import Path
import click

from betterci.runner import run_dag, load_workflow


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
@click.option(
    "--workers",
    default=None,
    type=int,
    help="Number of parallel workers",
)
@click.option(
    "--cache-dir",
    default=".betterci/cache",
    help="Cache directory",
)
@click.option(
    "--fail-fast/--no-fail-fast",
    default=True,
    help="Stop scheduling new jobs after first failure",
)
def run(workflow, workers, cache_dir, fail_fast):
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
        )

        click.echo("\nResults:")
        for job, status in results.items():
            click.echo(f"  {job}: {status}")

        if any(v == "failed" for v in results.values()):
            sys.exit(1)

    except Exception as e:
        click.echo(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
