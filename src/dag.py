# dag.py
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List, Set, Tuple

from model import Job


def build_dag(jobs: List[Job]) -> Tuple[Dict[str, Set[str]], Dict[str, int]]:
    """
    Build a DAG from Job objects.

    Requires:
      - job.name: str (unique)
      - job.needs: iterable[str] (names of jobs that must run BEFORE this job)
    """
    names = [j.name for j in jobs]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"Duplicate job names found: {dupes}")

    name_set = set(names)
    adj: Dict[str, Set[str]] = {n: set() for n in name_set}
    indeg: Dict[str, int] = {n: 0 for n in name_set}

    for job in jobs:
        deps = getattr(job, "needs", None) or []
        for dep in deps:
            if dep not in name_set:
                raise ValueError(
                    f"Job '{job.name}' depends on missing job '{dep}'. "
                    f"Known jobs: {sorted(name_set)}"
                )
            # edge dep -> job.name
            if job.name not in adj[dep]:
                adj[dep].add(job.name)
                indeg[job.name] += 1

    return adj, indeg


def topo_levels(adj: Dict[str, Set[str]], indeg: Dict[str, int]) -> List[List[str]]:
    """
    Convert DAG into topological "levels" (stages).
    Each stage can run in parallel.
    """
    indeg = dict(indeg)  # copy (we mutate it)
    q = deque(sorted([n for n, d in indeg.items() if d == 0]))

    levels: List[List[str]] = []
    processed = 0

    while q:
        level_size = len(q)
        level: List[str] = []

        for _ in range(level_size):
            node = q.popleft()
            level.append(node)
            processed += 1

            for child in sorted(adj.get(node, set())):
                indeg[child] -= 1
                if indeg[child] == 0:
                    q.append(child)

        levels.append(level)

    if processed != len(indeg):
        remaining = sorted([n for n, d in indeg.items() if d > 0])
        raise ValueError(f"DAG has a cycle (or unresolved deps). Stuck nodes: {remaining}")

    return levels


def run_dag_pipeline(
    jobs: Iterable[Job],
    run_fn: Callable[[Job], None],
    max_workers: int | None = None,
) -> None:
    """
    Scheduler + orchestrator (Option A):

    - Computes a valid execution order from dependencies (DAG).
    - Runs each stage in parallel.
    - Calls run_fn(job) for actual execution.
    - On first failure, stops and raises the exception.
    """
    jobs = list(jobs)
    job_map = {job.name: job for job in jobs}

    adj, indeg = build_dag(jobs)
    levels = topo_levels(adj, indeg)

    for level_idx, level in enumerate(levels):
        print(f"=== Stage {level_idx + 1}: {level} ===")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(run_fn, job_map[name]): name for name in level}

            for future in as_completed(futures):
                job_name = futures[future]
                try:
                    future.result()
                    print(f"✓ {job_name}")
                except Exception as e:
                    print(f"✗ Job failed: {job_name}")
                    raise e


# -------------------------
# Self-test (optional)
# -------------------------
if __name__ == "__main__":
    """
    Run a quick DAG sanity test:

        setup
         /  \
      lint  unit
        \    /
        package
          |
         e2e (fails)

    Run:
      python dag.py
    """
    import time
    from dataclasses import dataclass

    # If your real model.Job requires steps/env/etc, you can still test DAG logic
    # by making a tiny compatible Job-like object here.
    #
    # BUT since we're importing Job from model above, this test assumes your Job
    # has at least: name (str), needs (list[str]).
    #
    # If your real Job requires other fields, uncomment the local dataclass below
    # and ALSO comment out `from model import Job` at the top for this self-test only.

    # @dataclass(frozen=True)
    # class Job:
    #     name: str
    #     needs: list[str]

    def test_run_fn(job: Job) -> None:
        print(f"Running {job.name}...")
        time.sleep(0.7)
        if job.name == "e2e":
            raise RuntimeError("Intentional failure to test error handling")
        print(f"{job.name} done.")

    test_jobs = [
        Job(name="setup", needs=[]),              # type: ignore[arg-type]
        Job(name="lint", needs=["setup"]),        # type: ignore[arg-type]
        Job(name="unit", needs=["setup"]),        # type: ignore[arg-type]
        Job(name="package", needs=["lint", "unit"]),  # type: ignore[arg-type]
        Job(name="e2e", needs=["package"]),       # type: ignore[arg-type]
    ]

    run_dag_pipeline(test_jobs, run_fn=test_run_fn, max_workers=4)
