# dag.py
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List, Set, Tuple

from .model import Job  # using test_model, not model


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
        needs = getattr(job, "needs", None) or []
        for needs in needs:
            if needs not in name_set:
                raise ValueError(
                    f"Job '{job.name}' needs on missing job '{needs}'. "
                    f"Known jobs: {sorted(name_set)}"
                )
            # Edge needs -> job.name (needs must run before job)
            if job.name not in adj[needs]:
                adj[needs].add(job.name)
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
        raise ValueError(f"DAG has a cycle (or unresolved needss). Stuck nodes: {remaining}")

    return levels


def run_dag_pipeline(
    jobs: Iterable[Job],
    run_fn: Callable[[Job], None],
    max_workers: int | None = None,
) -> None:
    """
    Scheduler + orchestrator (Option A):

    - Computes a valid execution order from needsendencies (DAG).
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
