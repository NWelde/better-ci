# runner.py
from __future__ import annotations

import os
import runpy
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
import sys
from .model import Job, Step
from .cache import CacheStore, CacheHit
from fnmatch import fnmatch

from .git_facts.git import repo_root, head_sha, is_dirty, merge_base, changed_files as changed_files_between

# local dev ---> commit ---> CI ---> push ---> cloud CI


@dataclass
class CIError(Exception):
    """
    Structured CI error with enough context for:
      - clean CLI output
      - future UI rendering
      - debugging without full tracebacks
    """
    kind: str
    job: str
    step: str | None
    message: str
    details: dict

    def __str__(self) -> str:
        lines = [f"{self.kind}: {self.message}", f"job={self.job}"]
        if self.step:
            lines.append(f"step={self.step}")
        for k, v in self.details.items():
            lines.append(f"{k}={v}")
        return "\n".join(lines)


TOOL_HINTS = {
    "npm": "Install Node.js (includes npm) or fix PATH.",
    "node": "Install Node.js or fix PATH.",
    "pytest": "Install pytest (e.g., pip install pytest).",
    "ruff": "Install ruff (e.g., pip install ruff).",
    "docker": "Install Docker and ensure the daemon is running.",
    "python3": "Install Python 3 or fix PATH (python3).",
}




def git_functionality(
    compare_ref: str = "origin/main",
) -> Tuple[Optional[str], List[str]]:
    """
    Returns:
      recent_commit_head:
        - full SHA for HEAD if repo is clean
        - None if repo has uncommitted changes (dirty)
      changed_files:
        - list of changed file paths relative to repo root
    """
    root: Path = repo_root()
    original_cwd = os.getcwd()

    try:
        os.chdir(root)

        dirty = is_dirty()
        recent_commit_head: Optional[str] = None if dirty else head_sha()

        if dirty:
            # If dirty, include:
            # - staged changes
            # - unstaged changes
            # - untracked files
            files = set()

            # Unstaged + staged (compared to HEAD)
            # --name-only gives only paths, -z safer, but we'll keep it simple here.
            import subprocess

            unstaged = subprocess.check_output(
                ["git", "diff", "--name-only"],
                cwd=root,
                text=True,
            ).strip()

            staged = subprocess.check_output(
                ["git", "diff", "--name-only", "--cached"],
                cwd=root,
                text=True,
            ).strip()

            untracked = subprocess.check_output(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=root,
                text=True,
            ).strip()

            if unstaged:
                files.update(unstaged.splitlines())
            if staged:
                files.update(staged.splitlines())
            if untracked:
                files.update(untracked.splitlines())

            changed = sorted(files)
        else:
            # If clean, compare HEAD against merge-base with compare_ref
            # Fall back to HEAD~1 if origin/main isn't available.
            try:
                base = merge_base(compare_ref)
            except Exception:
                # e.g. no remote configured, first commit, etc.
                base = "HEAD~1"

            try:
                changed = changed_files_between(base, "HEAD")
            except Exception:
                # If HEAD~1 doesn't exist (first commit), treat all tracked files as "changed"
                import subprocess
                tracked = subprocess.check_output(
                    ["git", "ls-files"],
                    cwd=root,
                    text=True,
                ).strip()
                changed = tracked.splitlines() if tracked else []

        return recent_commit_head, changed

    finally:
        os.chdir(original_cwd)
# ----------------------------------------------------------------------
# Workflow loading (local file/module)
# ----------------------------------------------------------------------

def load_workflow(path: str | Path) -> List[Job]:
    """
    Load a workflow from a python file path.

    The file must define either:
      - workflow() -> List[Job]
      - JOBS = [Job, ...]

    Returns:
      List[Job]
    """
    wf_path = Path(path).expanduser().resolve()
    if not wf_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {wf_path}")
    if wf_path.suffix != ".py":
        raise ValueError(f"Workflow must be a .py file, got: {wf_path.name}")

    module_name = f"betterci_workflow_{wf_path.stem}"
    globals_dict = runpy.run_path(str(wf_path), run_name=module_name)

    jobs = None
    if "workflow" in globals_dict and callable(globals_dict["workflow"]):
        try:
            jobs = globals_dict["workflow"]()
        except TypeError as e:
            if "positional arguments but" in str(e) and "was given" in str(e):
                raise TypeError(
                    "Your workflow() is being called with arguments (name collision with the helper). "
                    "Use the 'wf' helper instead: `from betterci import wf, job, sh` then "
                    "`def workflow(): return wf(job(...), job(...))`"
                ) from e
            raise
    elif "JOBS" in globals_dict:
        jobs = globals_dict["JOBS"]

    if not isinstance(jobs, list) or not all(isinstance(j, Job) for j in jobs):
        raise TypeError(
            "Workflow must return/define a List[Job]. "
            "Define workflow() -> List[Job] or JOBS = [Job, ...]."
        )

    return jobs



# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

@dataclass
class StepFailure(Exception):
    job: str
    step: str
    cmd: str
    exit_code: int

    def __str__(self) -> str:
        return f"[{self.job}] step '{self.step}' failed (exit={self.exit_code}): {self.cmd}"


# ----------------------------------------------------------------------
# Execution primitives
# ----------------------------------------------------------------------
def _run_step(job: Job, step: Step, repo_root: Path) -> None:
    cwd = (repo_root / (step.cwd or ".")).resolve()
    if not cwd.exists():
        raise FileNotFoundError(f"[{job.name}] step '{step.name}' cwd not found: {cwd}")

    env = os.environ.copy()
    env.update(getattr(job, "env", {}) or {})

    proc = subprocess.run(
        step.run,
        shell=True,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,   # so you can show output on failure
    )

    if proc.returncode != 0:
        raise StepFailure(
            job=job.name,
            step=step.name,
            cmd=step.run,
            exit_code=proc.returncode,
            stdout=proc.stdout[-4000:],  # Timeouts so the tests dont run forever
            stderr=proc.stderr[-4000:],
        )


def _run_job(job: Job, repo_root: Path, cache: CacheStore) -> Tuple[str, str]:
    """
    Returns (job_name, status) where status is:
      - "skipped(cache)"  (cache hit and skip_on_hit enabled)
      - "ok"
    Raises on failures.
    """
    cache_dirs = list(getattr(job, "cache_dirs", []) or [])
    skip_on_hit = bool(getattr(job, "cache_skip_on_hit", False))
    cache_enabled = bool(getattr(job, "cache_enabled", True))

    # ---- restore ----
    if cache_enabled and cache_dirs:
        hit: CacheHit = cache.restore(job, repo_root=repo_root)
        print(f"[{job.name}] cache: {hit.reason}")
        if hit.hit and skip_on_hit:
            return job.name, "skipped(cache)"

    # ---- run steps ----
    for step in job.steps:
        print(f"[{job.name}] ▶ {step.name}")
        _run_step(job, step, repo_root)

    # ---- save ----
    if cache_enabled and cache_dirs:
        key, _manifest = cache.save(job, repo_root=repo_root)
        keep = int(getattr(job, "cache_keep", 3))
        cache.prune(job.name, keep=keep)
        print(f"[{job.name}] cache: saved ({key[:12]}...)")

    return job.name, "ok"


# ----------------------------------------------------------------------
# DAG build (dependency graph)
# ----------------------------------------------------------------------

def _deps_of(job: Job) -> List[str]:
    """
    Canonical deps accessor.
    Prefer job.needs. If your model still uses 'dependency', this still works.
    """
    deps = getattr(job, "needs", None)
    if deps is None:
        deps = getattr(job, "dependency", None)
    return list(deps or [])


def _build_graph(jobs: List[Job]) -> Tuple[Dict[str, Job], Dict[str, Set[str]], Dict[str, int]]:
    by_name: Dict[str, Job] = {}
    for j in jobs:
        if j.name in by_name:
            raise ValueError(f"Duplicate job name: {j.name}")
        by_name[j.name] = j

    adj: Dict[str, Set[str]] = {name: set() for name in by_name}   # dep -> dependents
    indeg: Dict[str, int] = {name: 0 for name in by_name}          # in-degree per job

    for j in jobs:
        for d in _deps_of(j):
            if d not in by_name:
                raise ValueError(f"Job '{j.name}' depends on missing job '{d}'")
            adj[d].add(j.name)
            indeg[j.name] += 1

    return by_name, adj, indeg


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, p) for p in patterns)


def select_jobs(
    jobs: List[Job],
    *,
    use_git_diff: bool,
    compare_ref: str,
    print_plan: bool,
) -> List[Job]:
    if not use_git_diff:
        if print_plan:
            for j in jobs:
                print(f"✓ {j.name} (git diff disabled)")
        return list(jobs)

    _head, changed = git_functionality(compare_ref=compare_ref)
    changed_set = set(changed or [])

    selected: List[Job] = []
    for j in jobs:
        # per-job opt-out: always run
        if getattr(j, "diff_enabled", True) is False:
            selected.append(j)
            if print_plan:
                print(f"✓ {j.name} (diff disabled for job)")
            continue

        patterns = getattr(j, "paths", None)

        # no paths -> always run
        if not patterns:
            selected.append(j)
            if print_plan:
                print(f"✓ {j.name} (no paths specified)")
            continue

        hit = any(_matches_any(f, patterns) for f in changed_set)
        if hit:
            selected.append(j)
            if print_plan:
                print(f"✓ {j.name} (matched {patterns})")
        else:
            if print_plan:
                print(f"⏭ {j.name} (no match for {patterns})")

    return selected


def run_dag(
    jobs: List[Job],
    *,
    repo_root: str | Path = ".",
    cache_root: str | Path = ".betterci/cache",
    max_workers: int | None = None,
    fail_fast: bool = True,
    use_git_diff: bool = False,
    compare_ref: str = "origin/main",
    print_plan: bool = True,
) -> Dict[str, str]:
    repo_root_p = Path(repo_root).resolve()
    cache = CacheStore(cache_root)

    jobs = select_jobs(
        jobs,
        use_git_diff=use_git_diff,
        compare_ref=compare_ref,
        print_plan=print_plan,
    )

    by_name, adj, indeg = _build_graph(jobs)
    ready: List[str] = [name for name, deg in indeg.items() if deg == 0]
    results: Dict[str, str] = {}
    failed = False

    if max_workers is None:
        c = os.cpu_count() or 2
        max_workers = max(1, c - 1)

    in_flight: Dict = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while ready or in_flight:
            # schedule all currently ready
            while ready and not (fail_fast and failed):
                name = ready.pop()
                fut = pool.submit(_run_job, by_name[name], repo_root_p, cache)
                in_flight[fut] = name

            if not in_flight:
                break

            # wait for one completion, then loop to schedule newly-ready jobs
            fut = next(as_completed(list(in_flight.keys())))
            name = in_flight.pop(fut)

            try:
                job_name, status = fut.result()
                results[job_name] = status
            except Exception as e:
                results[name] = "failed"
                print(str(e))
                failed = True

            # unlock dependents only if success or skipped(cache)
            if results[name] in ("ok", "skipped(cache)"):
                for nxt in adj[name]:
                    indeg[nxt] -= 1
                    if indeg[nxt] == 0:
                        ready.append(nxt)
            else:
                failed = True

    return results


if __name__ == "__main__":
    jobs = load_workflow("betterci_workflow")
    res = run_dag(jobs)
    # simple exit code behavior
    if any(v == "failed" for v in res.values()):
        raise SystemExit(1)
    raise SystemExit(0)
