# runner.py
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

from model import Job, Step
from cache import CacheStore, CacheHit


@dataclass
class StepFailure(Exception):
    job: str
    step: str
    cmd: str
    exit_code: int

    def __str__(self) -> str:
        return f"[{self.job}] step '{self.step}' failed (exit={self.exit_code}): {self.cmd}"


def _run_step(job: Job, step: Step, repo_root: Path) -> None:
    cwd = repo_root / (step.cwd or ".")
    env = os.environ.copy()
    env.update(getattr(job, "env", {}) or {})

    # Stream output directly to console for now (fast + simple)
    proc = subprocess.run(
        step.run,
        shell=True,
        cwd=str(cwd),
        env=env,
    )
    if proc.returncode != 0:
        raise StepFailure(job=job.name, step=step.name, cmd=step.run, exit_code=proc.returncode)


def _run_job(job: Job, repo_root: Path, cache: CacheStore) -> Tuple[str, str]:
    """
    Returns (job_name, status) where status is: "skipped(cache)", "ok", "failed"
    """
    # ---- cache restore (optional) ----
    cache_dirs = list(getattr(job, "cache_dirs", []) or [])
    skip_on_hit = bool(getattr(job, "cache_skip_on_hit", False))

    if cache_dirs:
        hit: CacheHit = cache.restore(job, repo_root=repo_root)
        print(f"[{job.name}] cache: {hit.reason}")
        if hit.hit and skip_on_hit:
            return job.name, "skipped(cache)"

    # ---- execute steps ----
    for step in job.steps:
        print(f"[{job.name}] ▶ {step.name}")
        _run_step(job, step, repo_root)

    # ---- cache save (optional) ----
    # Save only if cache_dirs specified; CacheStore.save() is safe if dirs missing.
    if cache_dirs:
        key, manifest = cache.save(job, repo_root=repo_root)
        # optional pruning to keep cache small
        keep = int(getattr(job, "cache_keep", 3))
        cache.prune(job.name, keep=keep)
        print(f"[{job.name}] cache: saved ({key[:12]}...)")

    return job.name, "ok"


def _build_graph(jobs: List[Job]) -> Tuple[Dict[str, Job], Dict[str, Set[str]], Dict[str, int]]:
    by_name: Dict[str, Job] = {}
    for j in jobs:
        if j.name in by_name:
            raise ValueError(f"Duplicate job name: {j.name}")
        by_name[j.name] = j

    adj: Dict[str, Set[str]] = {name: set() for name in by_name}
    indeg: Dict[str, int] = {name: 0 for name in by_name}

    for j in jobs:
        deps = list(getattr(j, "dependency", []) or [])  # your field name
        for d in deps:
            if d not in by_name:
                raise ValueError(f"Job '{j.name}' depends on missing job '{d}'")
            adj[d].add(j.name)
            indeg[j.name] += 1

    return by_name, adj, indeg


def run_dag(
    jobs: List[Job],
    *,
    repo_root: str | Path = ".",
    cache_root: str | Path = ".betterci/cache",
    max_workers: int | None = None,
    fail_fast: bool = True,
) -> Dict[str, str]:
    """
    Executes jobs respecting dependencies, with caching integration.
    Returns {job_name: status}.
    """
    repo_root_p = Path(repo_root).resolve()
    cache = CacheStore(cache_root)

    by_name, adj, indeg = _build_graph(jobs)

    # initial ready queue
    ready = [name for name, deg in indeg.items() if deg == 0]
    results: Dict[str, str] = {}
    failed = False

    if max_workers is None:
        # leave 1 core free by default
        c = os.cpu_count() or 2
        max_workers = max(1, c - 1)

    in_flight: Dict = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while ready or in_flight:
            # schedule all currently-ready jobs
            while ready and not (fail_fast and failed):
                name = ready.pop()
                job = by_name[name]
                fut = pool.submit(_run_job, job, repo_root_p, cache)
                in_flight[fut] = name

            if not in_flight:
                break

            # wait for at least one to finish
            for fut in as_completed(list(in_flight.keys()), timeout=None):
                name = in_flight.pop(fut)
                try:
                    job_name, status = fut.result()
                    results[job_name] = status
                    if status == "failed":
                        failed = True
                except Exception as e:
                    results[name] = "failed"
                    print(str(e))
                    failed = True

                # Release dependents only if this job succeeded or was skipped(cache)
                if results[name] in ("ok", "skipped(cache)"):
                    for nxt in adj[name]:
                        indeg[nxt] -= 1
                        if indeg[nxt] == 0:
                            ready.append(nxt)
                else:
                    # On failure, downstream jobs will never become runnable
                    pass

                # break after one completion so we can reschedule newly-ready jobs
                break

    return results
