# step_workflows/test.py
"""
Typed test step compiler.

Expands a Step with kind='test' into one or more concrete shell Steps.
Called by the runner before execution begins — no kind='test' step ever
reaches _run_step(); they are always expanded first.

Usage via the DSL:
    from betterci import job, test

    job(
        "test",
        test("Run pytest", framework="pytest", args="-q"),
    )

    job(
        "test-js",
        test("Run npm tests", framework="npm", install=True),
    )
"""
from __future__ import annotations

from betterci.dsl import sh
from betterci.model import Step


def compile_test(step: Step) -> list[Step]:
    """
    Compile a typed test step (kind='test') into concrete shell steps.

    The runner calls this once per job during step expansion, before any
    steps are executed.
    """
    if step.kind != "test":
        raise ValueError(
            f"compile_test() called with step.kind={step.kind!r}; expected 'test'."
        )

    data = step.data or {}
    framework = data.get("framework")
    args = (data.get("args") or "").strip()
    install = bool(data.get("install", True))
    cwd = step.cwd

    if framework == "pytest":
        out: list[Step] = []
        if install:
            out.append(sh("Install dependencies", "pip install -e .[test] 2>/dev/null || pip install -r requirements.txt", cwd=cwd))
        out.append(sh(step.name, f"pytest {args}".strip(), cwd=cwd))
        return out

    if framework == "npm":
        out = []
        if install:
            out.append(sh("Install dependencies", "npm ci", cwd=cwd))
        out.append(sh(step.name, f"npm test {args}".strip(), cwd=cwd))
        return out

    raise ValueError(
        f"Unknown test framework: {framework!r}. "
        "Supported frameworks: 'pytest', 'npm'."
    )
