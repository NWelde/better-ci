from __future__ import annotations

from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable
import subprocess
import os
import sys

# import your existing models
from model import Job, Step


# -------------------------
# Errors
# -------------------------

class CycleError(Exception):
    pass


class StepFailed(Exception):
    pass


# -------------------------
# DAG construction
# -------------------------

def build_dag(jobs: Iterable[Job]):
    """
    Builds adjacency list + indegree map.
    Edge direction: dep -> job
    """
    adj: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {}

    job_map = {job.name: job for job in jobs}

    # initialize nodes
    for job in jobs:
        indeg.setdefault(job.name, 0)
        for dep in job.dependency:
            indeg.setdefault(dep, 0)

    # add edges
    for job in jobs:
        for dep in job.dependency:
            if dep not in job_map:
                raise KeyError(f"Job '{job.name}' depends on unknown job '{dep}'")
            adj[dep].append(job.name)
            indeg[job.name] += 1

    return adj, indeg


def topo_levels(adj: dict[str, list[str]], indeg: dict[str, int]) -> list[list[str]]:
    """
    Kahn's algorithm, but grouped into parallel levels.
    """
    indeg = dict(indeg)
    levels: list[list[str]] = []

    current = [n for n, d in indeg.items() if d == 0]
    visited = 0

    while current:
        levels.append(current)
        next_level = []

        for u in current:
            visited += 1
            for v in adj.get(u, []):
                indeg[v] -= 1
                if indeg[v] == 0:
                    next_level.append(v)

        current = next_level

    if visited != len(indeg):
        cycle_nodes = [n for n, d in indeg.items() if d > 0]
        raise CycleError(f"Cycle detected involving: {cycle_nodes}")

    return levels


# -------------------------
# Execution
# -------------------------

def run_step(step: Step, env: dict[str, str]):
    print(f"    → {step.name}")

    result = subprocess.run(
        step.run,
        shell=True,
        cwd=step.cwd,
        env={**os.environ, **env},
    )

    if result.returncode != 0:
        raise StepFailed(f"Step failed: {step.name}")


def run_job(job: Job):
    print(f"\n Job: {job.name}")

    for step in job.steps:
        run_step(step, job.env)

    print(f" Job completed: {job.name}")


def run_dag_pipeline(jobs: Iterable[Job], max_workers: int | None = None):
    """
    Executes jobs respecting DAG dependencies.
    Jobs in the same level run in parallel.
    """
    jobs = list(jobs)
    job_map = {job.name: job for job in jobs}

    adj, indeg = build_dag(jobs)
    levels = topo_levels(adj, indeg)

    for level_idx, level in enumerate(levels):
        print(f"\n=== Stage {level_idx + 1}: {level} ===")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(run_job, job_map[name]): name
                for name in level
            }

            for future in as_completed(futures):
                job_name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"\n✗ Job failed: {job_name}")
                    raise e


# -------------------------
# Optional: example usage
# -------------------------

if __name__ == "__main__":
    # minimal example assuming your Job / Step classes exist

    jobs = [
        Job(
            name="lint",
            steps=[Step("flake8", "echo linting")],
        ),
        Job(
            name="build",
            steps=[Step("build", "echo building")],
        ),
        Job(
            name="test",
            dependency=["lint", "build"],
            steps=[Step("pytest", "echo testing")],
        ),
        Job(
            name="deploy",
            dependency=["test"],
            steps=[Step("deploy", "echo deploying")],
        ),
    ]

    run_dag_pipeline(jobs)

#TODO: I have to make sure the dag.py has top notch error handling because this is going to be the map in which my ci is based, so this should be abel to catch parallel jobs trying to be executed together but dont have the same met dependecies this is something i have to consider