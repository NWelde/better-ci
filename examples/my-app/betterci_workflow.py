"""
BetterCI workflow for my-app.

Run it:
    cd examples/my-app
    pip install -e .[dev]
    pip install betterci         # or: pip install -e ../..

    betterci run                               # run all jobs
    betterci run --git-diff                    # only jobs affected by your changes
    betterci run --verbose                     # stream step output in real-time
    betterci run --safe                        # enforce constrained execution model
    betterci run --git-diff --print-plan       # see which jobs will run and why

This workflow demonstrates:
  - paths=   : git-diff scoping per job
  - needs=   : job dependency ordering
  - secrets= : early env var validation
  - requires=: tool pre-flight checks
  - cache_dirs / cache_skip_on_hit: transparent caching
  - test()   : typed test step (expands to pip install + pytest)
  - lint_step(): structured lint step with tool + args
"""
from betterci import job, sh, test, wf
from betterci.dsl import lint_step

# Source files that, when changed, should trigger lint and test.
SRC_PATHS = ["src/**/*.py"]
TEST_PATHS = ["tests/**/*.py"]
CONFIG_PATHS = ["pyproject.toml"]


def workflow():
    return wf(

        # ------------------------------------------------------------------
        # Lint: check code style whenever source files change.
        #
        # Demonstrates:
        #   - lint_step()  : structured lint step (tool + args, no shell string)
        #   - paths=       : only runs when src/ Python files change
        #   - requires=    : pre-flight check that ruff is installed
        # ------------------------------------------------------------------
        job(
            "lint",
            lint_step("Ruff — check style", "ruff", "check src/ tests/"),
            paths=SRC_PATHS + TEST_PATHS + CONFIG_PATHS,
            requires=["ruff"],
        ),

        # ------------------------------------------------------------------
        # Test: run the full pytest suite whenever source or tests change.
        #
        # Demonstrates:
        #   - test()              : typed step — expands to "pip install" + "pytest"
        #   - needs=["lint"]      : only runs after lint passes
        #   - cache_dirs       : saves/restores .venv; skipped on re-runs
        #   - cache_skip_on_hit : skip all steps entirely when cache key hasn't changed
        #   - inputs=           : include pyproject.toml in cache key so cache busts
        #                           when dependencies change
        # ------------------------------------------------------------------
        job(
            "test",
            test(
                "Run pytest",
                framework="pytest",
                args="-q --tb=short",
                install=True,
            ),
            needs=["lint"],
            paths=SRC_PATHS + TEST_PATHS + CONFIG_PATHS,
            inputs=["pyproject.toml", "src/**/*.py", "tests/**/*.py"],
            cache_dirs=[".venv"],
            cache_skip_on_hit=False,  # always run tests, but restore .venv from cache
            requires=["python3"],
        ),

        # ------------------------------------------------------------------
        # Type-check (optional example of a plain sh() job).
        #
        # This job is deliberately simple — it just shows that you can have
        # as many jobs as you like, each scoped to the files it cares about.
        # Remove or comment it out if mypy isn't installed.
        # ------------------------------------------------------------------
        job(
            "type-check",
            sh("mypy",
               "python3 -m mypy src/ --ignore-missing-imports "
               "--no-error-summary 2>/dev/null || true"),
            needs=["lint"],
            paths=SRC_PATHS + CONFIG_PATHS,
            # No requires= for mypy — this job degrades gracefully if it's absent.
        ),

        # ------------------------------------------------------------------
        # Build: package the project.
        #
        # Demonstrates:
        #   - needs=["test", "type-check"]   : only runs after both checks pass
        #   - paths=                         : only when source or config changes
        #   - secrets=                       : declares PYPI_TOKEN as required
        #                                      (BetterCI validates it before running)
        #
        # NOTE: We check for the secret but don't actually upload — this is
        # just to demonstrate the secrets= feature. Comment out secrets= to
        # run this job without PYPI_TOKEN set.
        # ------------------------------------------------------------------
        job(
            "build",
            sh("clean", "rm -rf dist/ build/"),
            sh("build",
               "python3 -m build --wheel --sdist 2>/dev/null || python3 -m build"),
            sh("list",  "ls dist/"),
            needs=["test", "type-check"],
            paths=SRC_PATHS + CONFIG_PATHS,
            inputs=["src/**/*.py", "pyproject.toml"],
            # Uncomment to require PYPI_TOKEN before running:
            # secrets=["PYPI_TOKEN"],
        ),
    )
