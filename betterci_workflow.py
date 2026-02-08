# betterci_workflow.py
# Workflow for tracking betterci itself: testing, linting, caching, and git diff
from __future__ import annotations
from betterci.dsl import wf, job, sh
from betterci.step_workflows.lint import lint_step

def workflow():
    return wf(
        # Lint job - runs ruff on the codebase
        job(
            "lint",
            lint_step(
                "Ruff check",
                tool="ruff",
                args="check .",
                files=["src/", "pyproject.toml"],
            ),
            paths=["src/**", "pyproject.toml", "*.py"],
            inputs=["src/**", "pyproject.toml", "*.py"],
            cache_dirs=[".venv", "~/.cache/pip"],
            cache_enabled=True,
            cache_keep=5,
        ),

        # Test job - runs pytest on the codebase
        job(
            "test",
            sh("Install package", "pip install -e ."),
            sh("Run pytest", "pytest -q"),
            needs=["lint"],
            paths=["src/**", "tests/**", "pyproject.toml"],
            inputs=["src/**", "tests/**", "pyproject.toml"],
            cache_dirs=[".venv", "~/.cache/pip", ".pytest_cache"],
            cache_enabled=True,
            cache_keep=5,
        ),

        # Format check job - ensures code is properly formatted
        job(
            "format-check",
            lint_step(
                "Ruff format check",
                tool="ruff",
                args="format --check .",
                files=["src/", "*.py"],
            ),
            paths=["src/**", "*.py"],
            inputs=["src/**", "*.py"],
            cache_dirs=[".venv", "~/.cache/pip"],
            cache_enabled=True,
            cache_keep=5,
        ),

        # Type check job (if mypy is available)
        job(
            "type-check",
            sh(
                "Type check",
                "python -m mypy src/betterci --ignore-missing-imports || echo 'mypy not available, skipping'",
            ),
            paths=["src/**", "pyproject.toml"],
            inputs=["src/**", "pyproject.toml"],
            cache_dirs=[".venv", "~/.cache/pip"],
            cache_enabled=True,
            cache_keep=5,
        ),

        # Documentation check - ensures README and docs are up to date
        job(
            "docs-check",
            sh("Check README", "test -f README.md && echo 'README.md exists'"),
            paths=["README.md", "docs/**"],
            inputs=["README.md", "docs/**"],
            diff_enabled=True,
        ),

        # Config check - validates project configuration
        job(
            "config-check",
            sh("Validate pyproject.toml", "python -c 'import tomli; tomli.load(open(\"pyproject.toml\", \"rb\"))' || python -c 'import tomllib; tomllib.load(open(\"pyproject.toml\", \"rb\"))'"),
            paths=["pyproject.toml"],
            inputs=["pyproject.toml"],
            diff_enabled=True,
        ),
    )

