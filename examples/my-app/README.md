# my-app — BetterCI example project

A small Python calculator library used to demonstrate BetterCI features.

## Setup

```bash
cd examples/my-app

# Install the app and its dev dependencies
pip install -e .[dev]

# Install BetterCI (from repo root, or via pip)
pip install -e ../..
```

## Run the workflow

```bash
# Run all jobs
betterci run

# Only run jobs affected by files you changed
betterci run --git-diff

# Stream step output in real-time
betterci run --verbose

# See exactly which jobs would run (and why) without executing
betterci run --git-diff --print-plan

# Enforce the constrained execution model (only betterci imports allowed)
betterci run --safe
```

## What's in the workflow

| Job | What it does | Triggered by |
|---|---|---|
| `lint` | Runs `ruff check` on all Python files | `src/**`, `tests/**`, `pyproject.toml` |
| `test` | Runs `pytest` (with pip install + .venv caching) | `src/**`, `tests/**`, `pyproject.toml` |
| `type-check` | Runs `mypy` on `src/` | `src/**`, `pyproject.toml` |
| `build` | Builds a wheel + sdist | `src/**`, `pyproject.toml` |

## Features demonstrated

- **`paths=`** — each job only runs when its declared files change (`--git-diff`)
- **`needs=`** — `test` and `type-check` wait for `lint`; `build` waits for both
- **`requires=`** — `lint` checks that `ruff` is installed before running any steps
- **`secrets=`** — `build` shows how to declare required env vars (commented out; uncomment `secrets=["PYPI_TOKEN"]` to try it)
- **`cache_dirs=`** — `test` saves `.venv` so `pip install` is skipped on re-runs
- **`test()`** — typed test step that expands to `pip install` + `pytest`
- **`lint_step()`** — structured lint step (tool + args, no raw shell string)
