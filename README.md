# BetterCI

> **A local CI runner that shows you exactly why each job ran — and why it didn't.**

---

## 🤔 What is BetterCI?

When developers push code to a project, an automated system usually runs tests, checks code style (linting), and builds the software to make sure nothing is broken. This is called **Continuous Integration (CI)**.

Most CI systems run in the cloud (like GitHub Actions or Jenkins). They try to save time by reusing previous results — a technique called **caching** — but it's often a black box: you can't easily tell why a job ran again, or why the cache was reused.

**BetterCI** is a local CI runner you can run on your own machine. You define your build steps in plain Python (no YAML files), and BetterCI runs them with transparent, deterministic caching. That means:

- You can always see **exactly** why a job ran.
- You can always see **exactly** why a cached result was reused.
- You can run **only the jobs affected by your code changes**, saving time on large projects.

---

## 🌟 Why BetterCI?

| Problem with traditional CI | How BetterCI helps |
|---|---|
| Cache invalidation is mysterious — you don't know why it re-ran | Cache keys are fully explicit and derived from your inputs |
| YAML config files are hard to read and write | Workflows are plain Python functions — no new syntax to learn |
| Running all jobs on every change is slow | `--git-diff` mode runs only the jobs affected by your changes |
| Debugging CI failures requires reading cloud logs | Everything runs locally; errors show job name, step name, and exit code |
| Cloud CI can be expensive for exploratory work | Run the full pipeline on your laptop for free |

---

## 🚀 Features at a Glance

- **📝 Workflows in Python** — Define jobs as Python functions using `workflow()` or a `JOBS` list. No YAML required.
- **🔒 Deterministic cache** — The cache key is computed from your declared inputs, environment variables, and tool versions. The same inputs always produce a cache hit — no guesswork or surprise re-runs.
- **🔍 Git-aware job selection** — Compare your changes against a branch (e.g., `origin/main`) and run only the jobs whose source files actually changed.
- **⚠️ Structured error messages** — When something fails, BetterCI reports the job name, step name, and exit code so you know exactly what went wrong.
- **☁️ Cloud option** — Submit workflows to a remote API; worker agents poll for jobs, execute them, and report logs back.

---

## 📋 Requirements

Before installing, make sure you have:

- **Python 3.10 or newer** — [Download Python](https://www.python.org/downloads/)
- **Git** — Required for `--git-diff` mode and automatic repository root detection. [Download Git](https://git-scm.com/)

---

## 📦 Installation

```bash
pip install -e .
```

After installation, the `betterci` command is available in your terminal.

---

## ⚡ Quick Start

> **Prerequisites:** Make sure you have Python 3.10+ and Git installed before continuing (see [Requirements](#-requirements) above).

### Step 1 — Create a workflow file

Create a file called `betterci_workflow.py` in your project root:

```python
from betterci import wf, job, sh

def workflow():
    return wf(
        job("lint", sh("lint", "ruff check .")),
        job("test", sh("test", "pytest -q"), needs=["lint"]),
    )
```

This defines two jobs:
- **lint** — runs `ruff check .` to check code style.
- **test** — runs `pytest -q` to run tests; it only starts **after** `lint` finishes successfully (`needs=["lint"]`).

### Step 2 — Run the workflow

```bash
betterci run --workflow betterci_workflow.py
```

### What to expect

You'll see output similar to:

```
[plan] Selected jobs: lint, test
[lint ] Running step: lint
[lint ] ✓ Done (0.42s)
[test ] Running step: test
[test ] ✓ Done (1.87s)
All jobs completed successfully.
```

If a job fails, BetterCI prints the job name, step name, and the exit code so you know exactly where to look.

> **Tip:** Any `.py` file that exposes a `workflow()` function or a `JOBS` list works as a workflow file.

---

## 🔧 Defining Workflows

### `job()` — Define a CI job

```python
job(name, *steps, needs=None, paths=None, inputs=None, env=None, cache_dirs=None, ...)
```

| Parameter | What it does |
|---|---|
| `name` | A unique name for this job (e.g., `"test"`, `"build"`). |
| `*steps` | One or more steps to run (created with `sh()` or similar). |
| `needs` | List of job names that must finish before this job starts (e.g., `["lint"]`). |
| `paths` | File glob patterns (e.g., `["src/**", "tests/**"]`). With `--git-diff`, only run this job if one of these files changed. |
| `inputs` | Paths whose file contents are hashed into the cache key — so the cache is invalidated when these files change. |
| `cache_dirs` | Directories to save and restore between runs (e.g., `[".venv", "node_modules"]`). Speeds up jobs with dependencies. |
| `env` | Extra environment variables for this job (e.g., `{"NODE_ENV": "test"}`). |

### `sh()` — Define a shell step

```python
sh(name, cmd, cwd=None)
```

Creates a step that runs a shell command. `cwd` sets the working directory (defaults to the repo root).

### `wf()` — Collect jobs into a workflow

```python
wf(*jobs)
```

Wraps a list of `job(...)` calls into a workflow object that BetterCI can execute.

### `build()` — Fluent job builder

For cases where you prefer a chainable API instead of keyword arguments:

```python
build("name")
    .depends_on("other-job")
    .define_step("step-name", "shell command")
    .with_inputs("src/")
    .cache_dirs(".venv")
    .build()
```

### `matrix()` — Run a job across multiple values

```python
matrix("py", ["3.10", "3.11"]).jobs(lambda v: job(f"test-{v}", sh("test", f"python{v} -m pytest")))
```

Generates one job per value in the list — useful for testing across multiple Python versions, OS targets, etc.

### Docker and lint steps

For Docker-based or lint steps, use `step_workflows`. Set the `workflow_type` on your step, and the runner automatically calls the right execution logic.

### Complete realistic example

The following example shows a project with lint, test, and build jobs — with caching and git-diff filtering enabled:

```python
from betterci import wf, job, sh

def workflow():
    return wf(
        # Run linting only when Python source files change
        job(
            "lint",
            sh("ruff", "ruff check src/"),
            paths=["src/**/*.py"],
        ),

        # Run tests only when source or test files change
        # Caches the virtual environment so pip install is skipped on re-runs
        job(
            "test",
            sh("install", "pip install -e .[test]"),
            sh("pytest", "pytest -q tests/"),
            needs=["lint"],
            paths=["src/**/*.py", "tests/**/*.py"],
            inputs=["pyproject.toml"],
            cache_dirs=[".venv"],
        ),

        # Build a distribution package after tests pass
        job(
            "build",
            sh("build", "python -m build"),
            needs=["test"],
            inputs=["src/**", "pyproject.toml"],
        ),
    )
```

---

## 💻 Commands

### `betterci run` — Run a workflow locally

```bash
betterci run --workflow betterci_workflow.py
```

| Flag | Description | Default |
|---|---|---|
| `--workflow` | Workflow name or path (adds `.py` automatically if missing). | `betterci_workflow` |
| `--workers` | Maximum number of jobs to run in parallel. | CPU count − 1 |
| `--cache-dir` | Directory to store cached job results. | `.betterci/cache` |
| `--fail-fast` / `--no-fail-fast` | Stop scheduling new jobs after the first failure. | `--fail-fast` |
| `--git-diff` / `--no-git-diff` | Only run jobs whose `paths` overlap with files changed in git. | `--no-git-diff` |
| `--compare-ref` | Branch or commit to compare against when using `--git-diff`. | `origin/main` |
| `--print-plan` / `--no-print-plan` | Print which jobs will run (and which are skipped) before executing. | `--print-plan` |

**Examples:**

```bash
# Run all jobs
betterci run --workflow betterci_workflow.py

# Only run jobs affected by changes since origin/main, using 4 workers
betterci run --workflow betterci_workflow.py --git-diff --compare-ref origin/main --workers 4

# Run without stopping on first failure, using a custom cache directory
betterci run --no-fail-fast --cache-dir /tmp/my-cache
```

---

### `betterci submit` — Submit a workflow to the cloud API

```bash
betterci submit --api http://localhost:8000 --workflow betterci_workflow.py
```

| Flag | Description | Default |
|---|---|---|
| `--api` | Base URL of the cloud API (**required**). | — |
| `--workflow` | Workflow name or path. | `betterci_workflow` |
| `--repo` | Repository URL to pass to the agent. | git remote `origin` URL |
| `--ref` | Git branch, tag, or commit SHA to run. | Current branch or `HEAD` |

**Example:**

```bash
betterci submit --api https://ci.example.com --workflow betterci_workflow.py --ref main
```

---

### `betterci agent` — Start a worker agent

An agent polls the cloud API for submitted jobs, executes them, and reports results back.

```bash
betterci agent --api http://localhost:8000
```

| Flag | Description | Default |
|---|---|---|
| `--api` | Base URL of the cloud API (**required**). | — |
| `--agent-id` | A unique name for this agent instance. | System hostname |
| `--poll-interval` | Seconds to wait between polls when no jobs are queued. | `5` |

**Example:**

```bash
betterci agent --api https://ci.example.com --agent-id worker-1 --poll-interval 10
```

---

## ⚙️ How the Runner Works

Here is the end-to-end pipeline BetterCI follows when you run a workflow:

```
┌─────────────────────────────────────────────────────────────┐
│                        betterci run                         │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  1. LOAD                                                     │
│  Execute the workflow .py file.                              │
│  Call workflow() or read JOBS to get the list of jobs.       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  2. SELECT                                                   │
│  If --git-diff is set: compare changed files against the    │
│  configured ref. Skip jobs whose paths don't match.         │
│  Print the plan (selected / skipped) when --print-plan.     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  3. ORDER (DAG)                                              │
│  Sort jobs by their needs= dependencies so that each job    │
│  runs only after the jobs it depends on have finished.      │
│  Independent jobs run in parallel.                          │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  4. EXECUTE (per job)                                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ a. Check cache — compute key from job name, step    │   │
│  │    commands, env vars, tool versions, and hashes    │   │
│  │    of declared input files.                         │   │
│  │    → Cache hit?  Restore saved directories, skip.   │   │
│  │    → Cache miss? Continue to next step.             │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │ b. Run each step in order (shell command or         │   │
│  │    Docker / lint step workflow).                    │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │ c. On success — save cache_dirs as a compressed     │   │
│  │    archive. Remove old archives beyond cache_keep.  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**About the cache key:** The key is computed from the job name, the commands and options of each step, the job's environment variables, the versions of any required tools, and a hash of the files listed in `inputs`. If all of these are identical to a previous run, BetterCI considers it a cache hit and skips re-running the job. There is no guesswork — the same inputs always produce the same key.

---

## 🗂️ Project Layout

| Path | What it does | Good starting point? |
|---|---|---|
| `src/betterci/cli.py` | The main entry point for the `betterci` command. Defines all sub-commands (`run`, `submit`, `agent`) and their flags. | ✅ Start here to understand the CLI. |
| `src/betterci/runner.py` | The core engine: loads the workflow file, selects jobs by git diff, orders them by dependencies, runs them in parallel, and handles cache save/restore. | ✅ Start here to understand execution. |
| `src/betterci/cache.py` | Computes cache keys, saves and restores compressed job archives (`.tar.gz`), and prunes old ones. | For cache-related questions. |
| `src/betterci/dsl.py` | Defines the Python DSL functions: `job()`, `sh()`, `wf()`, `build()`, `matrix()`, and the `JobBuilder` helper class. | For workflow authoring questions. |
| `src/betterci/model.py` | Data classes for `Job` and `Step` — the core data structures passed between all other modules. | For understanding data shapes. |
| `src/betterci/dag.py` | Builds the dependency graph from `needs=` declarations and computes topological ordering (which jobs can run in parallel). | For dependency/ordering questions. |
| `src/betterci/git_facts/git.py` | Git utilities: detect repo root, get HEAD SHA, list changed files, compute merge-base, fetch remote URL, and get the current branch. | For git integration questions. |
| `src/betterci/agent/` | The agent loop that polls the cloud API for queued jobs, claims and executes them, and reports logs back. | For cloud/agent questions. |
| `src/betterci/step_workflows/` | Execution logic for Docker-based and lint steps, plus helpers for expanding test steps. | For custom step type questions. |
| `cloud/` | The cloud-side API server: queues workflow runs, assigns jobs to available agents, and stores execution logs. | For cloud API questions. |

---

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

1. **Fork** the repository on GitHub and clone your fork locally.
2. **Create a branch** for your change: `git checkout -b my-feature`.
3. **Make your changes** and add tests where appropriate.
4. **Run tests** to make sure everything passes.
5. **Open a Pull Request** against `main` with a clear description of what you changed and why.

Please keep pull requests focused — one feature or bug fix per PR makes review much easier.

---

## 📄 License

_License information not yet specified. See repository for details._

---

## 📖 Glossary

| Term | Plain-English meaning |
|---|---|
| **CI (Continuous Integration)** | An automated process that runs tests and checks on code every time a developer makes a change, to catch bugs early. |
| **Job** | A named unit of work in a workflow (e.g., "run tests" or "build the app"). A job contains one or more steps. |
| **Step** | A single command inside a job (e.g., `pytest -q`). |
| **Workflow** | A collection of jobs, their dependencies, and configuration — the full description of what BetterCI should do. |
| **DAG (Directed Acyclic Graph)** | A way of representing tasks and their dependencies so each task runs only after everything it depends on has finished, with no circular dependencies. |
| **Cache** | A saved snapshot of a job's output directories (e.g., installed packages). On the next run, if nothing changed, the snapshot is restored instead of re-running the job. |
| **Cache key** | A fingerprint computed from a job's inputs. If the key matches a previous run, the cached result is reused. |
| **Lint / Linting** | Automatically checking code for style problems and common mistakes (e.g., unused variables, formatting issues) using a tool like `ruff`. |
| **Git diff** | The list of files that changed between two points in the git history (e.g., between your branch and `main`). |
| **Ref** | A git reference — a pointer to a commit. Can be a branch name (e.g., `main`), a tag (e.g., `v1.0`), or a commit hash. |
| **Merge-base** | The most recent common ancestor commit between two branches — used to find exactly which files changed on your branch. |
| **Glob** | A pattern for matching file paths using wildcards, e.g., `src/**/*.py` matches all Python files inside `src/` and its subdirectories. |
| **Tar.gz** | A compressed archive file format (like a zip file). BetterCI uses `.tar.gz` files to store cached job directories. |
| **Prune** | Automatically deleting old cached archives to free up disk space, keeping only the most recent `N` copies. |

---

*Project by Nathan Weldegiorgis, Raymond Wang, Devon Krish, and Kamran Samudrala*
