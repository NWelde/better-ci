"""Tests for betterci.dsl — workflow authoring helpers."""
import pytest
from betterci.model import Job, Step
import betterci.dsl as dsl
from betterci.dsl import (
    sh, lint_step, docker_step,
    job, build, wf, workflow, matrix,
)
# Import as a non-test name so pytest doesn't collect it as a test function
dsl_test = dsl.test
make_test_step = dsl_test  # alias used in TestTestStep and TestExpandSteps


# ---------------------------------------------------------------------------
# sh()
# ---------------------------------------------------------------------------

class TestSh:
    def test_basic(self):
        s = sh("greet", "echo hello")
        assert isinstance(s, Step)
        assert s.name == "greet"
        assert s.run == "echo hello"
        assert s.cwd is None
        assert s.workflow_type is None

    def test_with_cwd(self):
        s = sh("build", "make", cwd="backend/")
        assert s.cwd == "backend/"


# ---------------------------------------------------------------------------
# test()
# ---------------------------------------------------------------------------

class TestTestStep:
    def test_pytest(self):
        s = make_test_step("Run tests", framework="pytest", args="-q")
        assert s.kind == "test"
        assert s.data["framework"] == "pytest"
        assert s.data["args"] == "-q"
        assert s.data["install"] is True

    def test_npm(self):
        s = make_test_step("JS tests", framework="npm", install=False)
        assert s.data["framework"] == "npm"
        assert s.data["install"] is False

    def test_cwd(self):
        s = make_test_step("tests", framework="pytest", cwd="backend/")
        assert s.cwd == "backend/"

    def test_is_step(self):
        assert isinstance(make_test_step("t", framework="pytest"), Step)


# ---------------------------------------------------------------------------
# lint_step()
# ---------------------------------------------------------------------------

class TestLintStep:
    def test_basic(self):
        s = lint_step("Ruff", "ruff", "check src/")
        assert s.workflow_type == "lint"
        assert s.meta["tool"] == "ruff"
        assert s.meta["args"] == "check src/"
        assert s.meta["files"] == []

    def test_with_files(self):
        s = lint_step("ESLint", "eslint", files=["src/", "tests/"])
        assert s.meta["files"] == ["src/", "tests/"]

    def test_no_args(self):
        s = lint_step("Ruff", "ruff")
        assert s.meta["args"] == ""

    def test_is_step(self):
        assert isinstance(lint_step("x", "ruff"), Step)


# ---------------------------------------------------------------------------
# docker_step()
# ---------------------------------------------------------------------------

class TestDockerStep:
    def test_basic(self):
        s = docker_step("Build", "make build", image="python:3.11-slim")
        assert s.workflow_type == "docker"
        assert s.meta["image"] == "python:3.11-slim"
        assert s.run == "make build"
        assert s.meta["volumes"] == []
        assert s.meta["env"] == {}
        assert s.meta["user"] == ""

    def test_full(self):
        s = docker_step(
            "Test",
            "pytest",
            image="myimage:latest",
            volumes=["/tmp:/tmp"],
            env={"DEBUG": "1"},
            user="1000:1000",
        )
        assert s.meta["volumes"] == ["/tmp:/tmp"]
        assert s.meta["env"] == {"DEBUG": "1"}
        assert s.meta["user"] == "1000:1000"


# ---------------------------------------------------------------------------
# job()
# ---------------------------------------------------------------------------

class TestJob:
    def test_minimal(self):
        j = job("lint", sh("ruff", "ruff check src/"))
        assert isinstance(j, Job)
        assert j.name == "lint"
        assert len(j.steps) == 1
        assert j.needs == []
        assert j.secrets == []
        assert j.cache_dirs == []

    def test_multiple_steps(self):
        j = job("test", sh("install", "pip install -e ."), sh("pytest", "pytest -q"))
        assert len(j.steps) == 2

    def make_test_steps_list(self):
        j = job("x", steps_list=[sh("a", "echo a"), sh("b", "echo b")])
        assert len(j.steps) == 2

    def make_test_steps_mixed(self):
        j = job("x", sh("c", "echo c"), steps_list=[sh("a", "echo a"), sh("b", "echo b")])
        assert len(j.steps) == 3

    def test_needs(self):
        j = job("test", sh("run", "pytest"), needs=["lint"])
        assert j.needs == ["lint"]

    def test_secrets(self):
        j = job("deploy", sh("push", "docker push"), secrets=["DOCKER_TOKEN"])
        assert j.secrets == ["DOCKER_TOKEN"]

    def test_requires(self):
        j = job("docker-build", sh("build", "docker build ."), requires=["docker"])
        assert j.requires == ["docker"]

    def test_cache(self):
        j = job(
            "install",
            sh("pip", "pip install -e ."),
            cache_dirs=[".venv"],
            cache_skip_on_hit=True,
            cache_keep=5,
        )
        assert j.cache_dirs == [".venv"]
        assert j.cache_skip_on_hit is True
        assert j.cache_keep == 5

    def test_paths(self):
        j = job("lint", sh("run", "ruff check ."), paths=["src/**/*.py"])
        assert j.paths == ["src/**/*.py"]

    def test_cwd_applied_to_steps(self):
        """job(cwd=) fills in cwd for steps that don't have one."""
        j = job("x", sh("a", "echo"), sh("b", "echo", cwd="sub/"), cwd="root/")
        assert j.steps[0].cwd == "root/"
        assert j.steps[1].cwd == "sub/"   # explicit cwd wins

    def test_no_steps_raises(self):
        with pytest.raises(ValueError, match="must have at least one step"):
            job("empty")

    def test_env(self):
        j = job("x", sh("r", "echo"), env={"FOO": "bar"})
        assert j.env == {"FOO": "bar"}


# ---------------------------------------------------------------------------
# build() — fluent builder
# ---------------------------------------------------------------------------

class TestJobBuilder:
    def test_basic(self):
        j = (
            build("test")
            .define_step("install", "pip install -e .")
            .define_step("run", "pytest -q")
            .build()
        )
        assert j.name == "test"
        assert len(j.steps) == 2

    def test_depends_on(self):
        j = build("test").define_step("r", "pytest").depends_on("lint", "typecheck").build()
        assert j.needs == ["lint", "typecheck"]

    def test_requires_secrets(self):
        j = build("deploy").define_step("r", "echo").requires_secrets("API_KEY", "TOKEN").build()
        assert j.secrets == ["API_KEY", "TOKEN"]

    def test_cache(self):
        j = (
            build("install")
            .define_step("r", "pip install -e .")
            .cache_dirs(".venv")
            .cache_behavior(skip_on_hit=True, keep=5)
            .build()
        )
        assert j.cache_dirs == [".venv"]
        assert j.cache_skip_on_hit is True
        assert j.cache_keep == 5

    def test_with_inputs(self):
        j = build("x").define_step("r", "echo").with_inputs("pyproject.toml").build()
        assert j.inputs == ["pyproject.toml"]

    def test_no_steps_raises(self):
        with pytest.raises(ValueError):
            build("empty").build()

    def test_add_step(self):
        j = build("x").add_step(sh("r", "echo hi")).build()
        assert j.steps[0].run == "echo hi"


# ---------------------------------------------------------------------------
# wf() / workflow
# ---------------------------------------------------------------------------

class TestWf:
    def test_returns_list(self):
        j1 = job("a", sh("r", "echo"))
        j2 = job("b", sh("r", "echo"))
        result = wf(j1, j2)
        assert result == [j1, j2]

    def test_alias(self):
        assert workflow is wf


# ---------------------------------------------------------------------------
# matrix()
# ---------------------------------------------------------------------------

class TestMatrix:
    def test_expands(self):
        jobs = matrix("py", ["3.10", "3.11", "3.12"]).jobs(
            lambda v: job(f"test-{v}", sh("run", f"python{v} -m pytest"))
        )
        assert len(jobs) == 3
        assert jobs[0].name == "test-3.10"
        assert jobs[2].name == "test-3.12"

    def test_single_value(self):
        jobs = matrix("env", ["prod"]).jobs(lambda v: job(v, sh("r", "echo")))
        assert len(jobs) == 1
