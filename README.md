<div align="center">

# BetterCI

**CI that runs locally, fails fast, and never lies about the cache.**

[![Build](https://img.shields.io/badge/build-passing-brightgreen?style=flat-square)](https://github.com/NWelde/better-ci)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-orange?style=flat-square)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://python.org)

</div>

---

## Table of Contents

- [Why BetterCI](#why-betterci)
- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Workflow DSL Reference](#workflow-dsl-reference)
- [CLI Reference](#cli-reference)
- [Recipes](#recipes)
- [Cloud Mode](#cloud-mode)
- [Contributing](#contributing)

---

## Why BetterCI

Traditional CI systems solve the wrong problem. They optimize for running in the cloud, so the feedback loop is: _push → wait 8 minutes → read remote logs → guess why it failed_. Cache invalidation is a black box. YAML configs sprawl into hundreds of lines. Every tiny change reruns the entire pipeline.

**BetterCI is different:**

- **Workflows are Python, not YAML.** Your IDE understands them. You can refactor, import, and test them.
- **The cache is deterministic.** The key is derived from your declared `inputs`, tool versions, and env vars — never from timestamps or undocumented heuristics.
- **Git-diff filtering is first class.** Jobs declare which files they care about. `--git-diff` runs only the jobs whose source files actually changed against your compare ref.
- **Preflight runs before any step.** Missing tool or secret? The job fails immediately with a clear message — not after 20 minutes of setup.
- **The entire pipeline runs on your laptop.** No pushing to trigger feedback. No cloud billing for exploratory work.

> **BetterCI vs. GitHub Actions / Jenkins:** Those are cloud orchestrators. BetterCI is your local development loop — the thing you run 50 times a day before you push. Think `make`, but with a DAG, caching, and real error messages.

---

## Key Features

| Feature | Description |
|---|---|
| **Python DSL** | Define jobs with `job()`, `sh()`, `wf()` — no YAML, no custom schema, full IDE support |
| **Deterministic cache** | SHA-256 key from job name + step commands + env vars + input file hashes. Same inputs = cache hit, every time |
| **Git-diff job selection** | `paths=["src/**/*.py"]` on a job + `--git-diff` = only run what changed |
| **DAG execution** | `needs=["lint"]` chains jobs; independent jobs run in parallel with `ThreadPoolExecutor` |
| **Fail-fast preflight** | `requires=["docker"]` and `secrets=["API_KEY"]` validated before the first step fires |
| **Typed step helpers** | `test()`, `lint_step()`, `docker_step()` encode intent; expanded to shell at runtime |
| **Fluent builder API** | `build("name").depends_on(...).cache_dirs(...).build()` for programmatic job construction |
| **Matrix jobs** | `matrix("py", ["3.10", "3.11", "3.12"]).jobs(...)` — one job per value, all parallel |
| **Constrained execution** | `--safe` mode AST-audits workflow files; rejects any import outside `betterci` |
| **Cloud scale-out** | `betterci submit` + `betterci agent` — submit workflows remotely, run on any number of workers |

---

## Quick Start

### Install

```bash
git clone https://github.com/NWelde/better-ci.git
cd better-ci
pip install -e .
```

### Write a workflow

Create `betterci_workflow.py` in your project root:

```python
from betterci import wf, job, sh

def workflow():
    return wf(
        job("lint", sh("check", "ruff check src/")),
        job("test", sh("run",   "pytest -q"), needs=["lint"]),
    )
```

### Run it

```bash
betterci run
```

```
┌ plan ──────────────────────────────────┐
│  ✓ lint   (no paths filter)            │
│  ✓ test   (no paths filter)            │
└────────────────────────────────────────┘
[lint] Running: check
[lint] ✓ Done  0.4s
[test] Running: run
[test] ✓ Done  1.9s

2 jobs  ·  2 ok  ·  0 failed  ·  2.3s total
```

### Run only what changed

```bash
betterci run --git-diff --compare-ref origin/main
```

Only jobs whose `paths` overlap with your diff are executed. Everything else is skipped with a reason.

---

## How It Works

```
betterci run
│
├─ LOAD     Execute workflow .py → call workflow() or read JOBS
│
├─ AUDIT    (--safe) AST-parse imports; reject anything outside betterci.*
│
├─ SELECT   (--git-diff) diff against compare-ref; drop jobs with no
│           matching path patterns; print plan
│
├─ ORDER    Topological sort on needs= → compute parallel stages
│
└─ EXECUTE  Per stage, submit all jobs to ThreadPoolExecutor
    │
    ├─ PREFLIGHT   shutil.which() each requires= tool
    │              os.environ check each secrets= var
    │              → CIError on first miss (job fails before step 1)
    │
    ├─ CACHE       Compute key: SHA-256(name + steps + env + tool versions
    │              + hash of every file in inputs)
    │              → Hit:  restore cache_dirs, skip steps if cache_skip_on_hit
    │              → Miss: proceed
    │
    ├─ EXPAND      Typed steps (kind="test") → concrete shell Steps
    │
    ├─ STEPS       Run each Step sequentially via subprocess
    │              verbose=True → Popen for real-time streaming
    │
    └─ CACHE SAVE  tar.gz cache_dirs → prune old archives (keep cache_keep)
```

**The cache key is fully reproducible.** There are no timestamps, no random salts, no registry lookups. If you run the same job twice with the same code, the second run is always a hit.

**Preflight fires before any shell command.** A missing secret will never waste your time getting halfway through a deploy step.

---

## Workflow DSL Reference

### `sh(name, cmd, *, cwd=None)` → `Step`

The fundamental building block. Runs `cmd` as a shell command.

```python
sh("build", "python -m build")
sh("test",  "pytest -q tests/", cwd="backend/")
```

### `job(name, *steps, ...)` → `Job`

```python
job(
    "deploy",
    sh("push", "docker push myimage:latest"),
    needs       = ["build"],            # run after build
    paths       = ["src/**", "Dockerfile"],  # --git-diff trigger
    inputs      = ["pyproject.toml"],   # bust cache when this changes
    env         = {"ENV": "production"},
    requires    = ["docker"],           # preflight: tool must be on PATH
    secrets     = ["DOCKER_TOKEN"],     # preflight: env var must be set
    cache_dirs  = [".venv"],            # save/restore across runs
    cache_skip_on_hit  = False,         # restore dirs but still run steps
    cache_keep         = 5,             # keep the 5 most recent archives
)
```

### `test(name, *, framework, args="", install=True, cwd=None)` → `Step`

A **typed** test step. Expanded to concrete shell steps at runtime — never reaches the executor as-is.

```python
# Expands to: ["python3 -m pip install -e .[test]", "python3 -m pytest -q --tb=short"]
test("Run pytest", framework="pytest", args="-q --tb=short", install=True)

# Expands to: ["npm ci", "npm test -- --coverage"]
test("JS tests", framework="npm", args="-- --coverage")
```

### `lint_step(name, tool, args="", *, files=None, cwd=None)` → `Step`

Routes to the lint step workflow. Stores tool metadata in `step.meta`.

```python
lint_step("Ruff", "ruff", "check src/ tests/")
lint_step("ESLint", "eslint", files=["src/", "tests/"])
```

### `docker_step(name, cmd, image, *, volumes=None, env=None, user=None, cwd=None)` → `Step`

Runs `cmd` inside a Docker container. Repo root is mounted at `/workspace`.

```python
docker_step(
    "Integration tests",
    "pytest tests/integration/",
    image   = "python:3.12-slim",
    volumes = ["/var/run/docker.sock:/var/run/docker.sock"],
    env     = {"DATABASE_URL": "postgresql://..."},
)
```

### `wf(*jobs)` → `List[Job]`

Collects jobs into a workflow list. Return this from `workflow()` or assign to `JOBS`.

### `matrix(key, values).jobs(fn)` → `List[Job]`

```python
matrix("py", ["3.10", "3.11", "3.12"]).jobs(
    lambda v: job(f"test-{v}", sh("run", f"python{v} -m pytest"))
)
# → [job("test-3.10", ...), job("test-3.11", ...), job("test-3.12", ...)]
```

### `build(name)` → `JobBuilder`

Fluent API for building jobs programmatically.

```python
(
    build("test")
    .depends_on("lint", "typecheck")
    .define_step("install", "pip install -e .[test]")
    .define_step("run",     "pytest -q")
    .with_inputs("pyproject.toml", "src/**/*.py")
    .cache_dirs(".venv")
    .cache_behavior(skip_on_hit=False, keep=3)
    .requires_secrets("TEST_DB_URL")
    .build()
)
```

---

## CLI Reference

### `betterci run`

```
betterci run [OPTIONS]
```

| Flag | Default | Description |
|---|---|---|
| `--workflow PATH` | `betterci_workflow` | Workflow file (`.py` appended if missing) |
| `--workers N` | CPU count − 1 | Max parallel jobs |
| `--cache-dir PATH` | `.betterci/cache` | Cache storage root |
| `--fail-fast / --no-fail-fast` | `--fail-fast` | Stop scheduling on first failure |
| `--git-diff / --no-git-diff` | off | Filter jobs by changed files |
| `--compare-ref REF` | `origin/main` | Ref to diff against |
| `--print-plan / --no-print-plan` | on | Show selection plan before running |
| `--verbose` | off | Stream step output in real-time (Popen) |
| `--safe` | off | Reject workflow files with non-betterci imports |
| `--debug` | off | Print full stack traces on error |

### `betterci submit`

```
betterci submit --api URL --workflow PATH [OPTIONS]
```

| Flag | Default | Description |
|---|---|---|
| `--api URL` | — | Cloud API base URL **(required)** |
| `--workflow PATH` | `betterci_workflow` | Workflow file |
| `--repo URL` | git remote `origin` | Repo URL for the agent to clone |
| `--ref REF` | current branch / HEAD | Branch, tag, or commit SHA to run |
| `--api-key KEY` | `$BETTERCI_API_KEY` | Authentication token |

### `betterci agent`

```
betterci agent --api URL [OPTIONS]
```

| Flag | Default | Description |
|---|---|---|
| `--api URL` | — | Cloud API base URL **(required)** |
| `--agent-id ID` | hostname | Unique agent identifier |
| `--poll-interval N` | `5` | Seconds between polls when queue is empty |
| `--debug` | off | Verbose agent output |

---

## Recipes

### Cache a virtualenv across runs

```python
job(
    "test",
    sh("install", "python3 -m pip install -e .[test]"),
    sh("pytest",  "python3 -m pytest -q"),
    inputs    = ["pyproject.toml"],    # bust cache when deps change
    cache_dirs = [".venv"],
)
```

### Skip a job entirely on cache hit

```python
job(
    "build",
    sh("compile", "python -m build"),
    inputs           = ["src/**/*.py", "pyproject.toml"],
    cache_dirs        = ["dist/"],
    cache_skip_on_hit = True,    # restore dist/ and skip all steps
)
```

### Scope jobs to changed files

```python
SRC   = ["src/**/*.py"]
TESTS = ["tests/**/*.py"]
CFG   = ["pyproject.toml"]

job("lint",  sh("ruff", "ruff check src/"),  paths=SRC + CFG)
job("test",  sh("run",  "pytest -q"),        paths=SRC + TESTS + CFG, needs=["lint"])
job("build", sh("pkg",  "python -m build"),  paths=SRC + CFG,         needs=["test"])
```

Run with `--git-diff` and only the jobs whose files changed will execute.

### Matrix: test across Python versions

```python
from betterci import wf, job, sh, matrix

def workflow():
    return wf(*matrix("py", ["3.10", "3.11", "3.12"]).jobs(
        lambda v: job(
            f"test-{v}",
            sh("run", f"python{v} -m pytest -q"),
            paths=["src/**", "tests/**"],
        )
    ))
```

### Require secrets before deploy

```python
job(
    "deploy",
    sh("push", "docker push myorg/myapp:$TAG"),
    requires = ["docker"],
    secrets  = ["DOCKER_TOKEN", "DEPLOY_ENV"],
    # Both are validated before the first shell command runs.
)
```

### Enforce the constrained execution model

```bash
betterci run --safe
```

Any import outside `betterci.*` in the workflow file raises `CIError(kind="unsafe_workflow")` immediately. Workflow files are descriptions — not scripts.

---

## Cloud Mode

BetterCI includes a FastAPI cloud backend for distributing runs across multiple agents.

### Start the stack

```bash
cp .env.example .env          # fill in DATABASE_URL, REDIS_URL, BETTERCI_API_KEY
docker-compose up
```

### Submit a workflow

```bash
betterci submit \
  --api  http://localhost:8000 \
  --workflow betterci_workflow.py \
  --ref  main \
  --api-key $BETTERCI_API_KEY
```

### Start workers

```bash
# On as many machines as you need:
betterci agent --api http://localhost:8000 --agent-id worker-1
```

Each agent:
1. Polls `POST /leases/claim` for an available job
2. Clones the repo at the declared `ref`
3. Executes the job using the same local runner
4. Reports status and logs to `POST /leases/{job_id}/complete`

Expired leases are automatically re-queued every 30 seconds.

### Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `BETTERCI_API_KEY` | If set, all write endpoints require `X-API-Key` header |
| `LEASE_SECONDS` | Job lease TTL in seconds (default: `600`) |
| `QUEUE_NAME` | Redis queue key (default: `betterci:queue`) |
| `NO_COLOR` | Disable ANSI output |
| `FORCE_COLOR` | Force ANSI output even when not a TTY |

---

## Project Layout

```
src/betterci/
├── cli.py              # Entry point — all commands and flags
├── runner.py           # Load → select → order → execute pipeline
├── dsl.py              # job(), sh(), test(), wf(), matrix(), build()
├── model.py            # Job and Step dataclasses
├── cache.py            # Cache key computation, tar.gz save/restore/prune
├── dag.py              # Topological sort for needs= dependencies
├── git_facts/git.py    # Changed files, merge-base, repo root, HEAD SHA
├── step_workflows/
│   ├── test.py         # Typed test step → shell step expansion
│   ├── lint.py         # Lint step execution
│   ├── docker.py       # Docker container step execution
│   └── artifacts.py    # Artifact save/load helpers
├── agent/
│   ├── agent.py        # Poll → claim → execute → complete loop
│   ├── api_client.py   # HTTP client for cloud API
│   └── executor.py     # Job execution + log capture for remote runs
└── ui/console.py       # ANSI output, timing, plan display

cloud/app/
├── main.py             # FastAPI: /runs, /leases, /health
├── models.py           # SQLAlchemy: Run, Job, Lease
├── redisq.py           # Redis queue operations
└── settings.py         # Config from environment

examples/my-app/        # Runnable example — lint, test, type-check, build
tests/                  # 119 unit + integration tests
```

---

## Contributing

BetterCI is a small, focused project and pull requests are welcome.

**Setup:**

```bash
git clone https://github.com/NWelde/better-ci.git
cd better-ci
pip install -e ".[test]"
pytest tests/ -q
```

**Guidelines:**
- Keep changes focused — one feature or fix per PR
- Add tests for new behaviour; all 119 existing tests must stay green
- Run `ruff check src/` before opening a PR

**Open a PR** against `main` with a clear description of what changed and why.

---

*Built by Nathan Weldegiorgis, Raymond Wang, Devon Krish, and Kamran Samudrala*
