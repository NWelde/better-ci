# BetterCI

When developers push code, something has to run tests, lint, and builds to make sure nothing broke—that’s continuous integration (CI). Usually it runs in the cloud and reuses previous work (caching), but you often can’t tell why a job ran or why the cache was used. BetterCI is a local CI runner that does the same job: you define what to run in Python, and it runs it with caching and optional “only run what changed” logic. The difference is the cache is explicit—you see exactly why something ran and why it was cached.

## Overview

Runs a DAG of jobs—steps (shell, Docker, lint), dependencies (`needs`), optional path filters. With `--git-diff`, only jobs whose `paths` match changed files run. Cache key = job + steps + env + tool versions + hash of declared inputs. Same inputs ⇒ cache hit; no heuristic invalidation.

- **Workflows in Python** — `workflow()` or `JOBS` in a module. No YAML.
- **Deterministic cache** — Keys from declared inputs, env, tool versions. Tar.gz per job; prune keeps last N.
- **Git-aware selection** — Diff against a ref (e.g. `origin/main`); run only jobs that touch changed files.
- **Structured errors** — Job name, step name, exit code.
- **Cloud option** — Submit workflows to an API; agents poll for jobs, execute them, and report logs.

## Requirements

- Python 3.10+
- Git (for `--git-diff` and repo root detection)

## Installation

```bash
pip install -e .
```

The CLI is available as `betterci`.

## Quick start

1. Create a workflow file (e.g. `betterci_workflow.py`) that defines a list of jobs:

```python
from betterci import wf, job, sh

def workflow():
    return wf(
        job("lint", sh("lint", "ruff check .")),
        job("test", sh("test", "pytest -q"), needs=["lint"]),
    )
```

2. Run it:

```bash
betterci run --workflow betterci_workflow.py
```

Any `.py` that exposes `workflow()` or `JOBS` works.

## Defining workflows

- **`job(name, *steps, needs=None, paths=None, inputs=None, env=None, cache_dirs=None, ...)`** — `needs` = jobs that must finish first. `paths` = globs for `--git-diff` (e.g. `["src/**", "tests/**"]`). `inputs` = paths that go into the cache key; `cache_dirs` = what we store/restore (e.g. `.venv`, `node_modules`).
- **`sh(name, cmd, cwd=None)`** — Shell step.
- **`wf(*jobs)`** — List of jobs.

Builder: `build("name").depends_on("other").define_step("step", "cmd").with_inputs("src/").cache_dirs(".venv").build()`. Matrix: `matrix("py", ["3.10", "3.11"]).jobs(lambda v: job(...))`. Docker/lint steps use `step_workflows`; set `workflow_type` and the runner calls the right `run_step`.

## Commands

**`betterci run`**

- `--workflow` — Workflow name or path (default: `betterci_workflow`; tries `.py` if missing).
- `--workers` — Max parallel jobs (default: CPU count − 1).
- `--cache-dir` — Cache root (default: `.betterci/cache`).
- `--fail-fast` / `--no-fail-fast` — Stop scheduling new jobs after first failure (default: true).
- `--git-diff` / `--no-git-diff` — Select jobs by changed files and `paths` (default: false).
- `--compare-ref` — Ref to diff against when using `--git-diff` (default: `origin/main`).
- `--print-plan` / `--no-print-plan` — Print which jobs are selected or skipped (default: true).

**`betterci submit`**

- `--api` — API base URL (required; e.g. `http://localhost:8000`).
- `--workflow` — Workflow name or path (default: `betterci_workflow`).
- `--repo` — Repository URL (default: git remote origin URL).
- `--ref` — Git ref/branch/commit (default: current branch or HEAD).

**`betterci agent`**

- `--api` — API base URL (required).
- `--agent-id` — Unique agent identifier (default: hostname).
- `--poll-interval` — Polling interval in seconds when idle (default: 5).

## How the runner works

1. **Load** — The workflow file is executed; `workflow()` or `JOBS` must evaluate to a list of `Job`.
2. **Select** — If `--git-diff` is set, only jobs whose `paths` match changed files (or that have `diff_enabled=False`) run; otherwise all jobs run. Selection is printed when `--print-plan` is true.
3. **DAG** — Jobs are ordered by `needs`. The runner builds an adjacency list and in-degrees, then runs jobs in parallel as their dependencies complete.
4. **Execute** — For each job: if the job has `cache_dirs` and caching is enabled, the cache is restored by key (job + steps + env + tool versions + input hashes). Then each step runs (shell or step_workflow). On success, cache_dirs are saved and old artifacts are pruned by `cache_keep`.

Cache key inputs are: job name, step names/commands/cwd, job env, versions of tools listed in `requires`, and the content hash of declared `inputs`. Excludes (e.g. `.git`, `__pycache__`) are applied when hashing and when building the tar.

## Project layout

| Path | Role |
|------|------|
| `src/betterci/cli.py` | CLI entrypoint; `betterci run`, `betterci submit`, `betterci agent` and options. |
| `src/betterci/runner.py` | Workflow loading, job selection (git diff), DAG build, parallel execution, cache restore/save, step execution (shell + step_workflows). |
| `src/betterci/cache.py` | Cache key computation, tar.gz store/restore, prune. |
| `src/betterci/dsl.py` | `job`, `sh`, `wf`, `build`, `matrix`; JobBuilder. |
| `src/betterci/model.py` | `Job`, `Step` dataclasses. |
| `src/betterci/dag.py` | DAG construction and topological levels. |
| `src/betterci/git_facts/git.py` | Repo root, HEAD SHA, dirty state, changed files, merge-base, remote URL, current ref. |
| `src/betterci/agent/` | Agent loop: poll API, claim jobs, run steps, report logs. |
| `src/betterci/step_workflows/` | Docker and lint step execution; test-step expansion helpers. |
| `cloud/` | Cloud API: queue runs, assign jobs to agents, store logs. |

## Project by Nathan Weldegiorgis, Raymond Wang, Devon Krish, and Kamran Samudrala