from __future__ import annotations

from betterci.dsl import wf, job, sh, test
from betterci.model import Step


def workflow():
    return wf(
        job(
            "test-py",
            test("Py tests", framework="pytest", args="-q", install=True),
            paths=["src/**", "tests/**", "requirements.txt", "pyproject.toml"],
            inputs=["src/**", "tests/**", "requirements.txt", "pyproject.toml"],
            cache_dirs=[".venv", "~/.cache/pip", ".pytest_cache"],
        ),
        job(
            "test-js",
            test("JS tests", framework="npm", args="", install=True),
            paths=["package.json", "package-lock.json", "frontend/**"],
            inputs=["package.json", "package-lock.json", "frontend/**"],
            cache_dirs=["node_modules", "~/.npm"],
        ),
    )

def compile_test(step: Step) -> list[Step]:
    """
    Turn a typed test step into runnable shell steps.
    Runner never sees kind='test' steps after compilation.
    """
    data = step.data or {}
    framework = data.get("framework")
    args = (data.get("args") or "").strip()
    install = bool(data.get("install", True))
    cwd = step.cwd

    if framework == "pytest":
        out: list[Step] = []
        if install:
            out.append(sh("Install (py)", "python -m pip install -r requirements.txt", cwd=cwd))
        out.append(sh(step.name, f"pytest {args}".strip(), cwd=cwd))
        return out

    if framework == "npm":
        out: list[Step] = []
        if install:
            out.append(sh("Install (js)", "npm ci", cwd=cwd))
        out.append(sh(step.name, f"npm test {args}".strip(), cwd=cwd))
        return out

    raise ValueError(f"Unknown framework: {framework!r}")
