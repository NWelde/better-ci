"""Tests for betterci.model — Job and Step data classes."""
import pytest
from betterci.model import Job, Step


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------

class TestStep:
    def test_minimal(self):
        s = Step(name="run", run="echo hi")
        assert s.name == "run"
        assert s.run == "echo hi"
        assert s.cwd is None
        assert s.kind is None
        assert s.data is None
        assert s.workflow_type is None
        assert s.meta == {}

    def test_with_all_fields(self):
        s = Step(
            name="lint",
            run="ruff check src/",
            cwd="backend/",
            workflow_type="lint",
            meta={"tool": "ruff", "args": "check src/"},
        )
        assert s.workflow_type == "lint"
        assert s.meta["tool"] == "ruff"
        assert s.cwd == "backend/"

    def test_typed_test_step(self):
        s = Step(name="tests", kind="test", data={"framework": "pytest", "args": "-q"})
        assert s.kind == "test"
        assert s.data["framework"] == "pytest"

    def test_frozen(self):
        """Step is immutable — direct assignment should raise."""
        s = Step(name="x", run="echo")
        with pytest.raises((AttributeError, TypeError)):
            s.name = "y"  # type: ignore[misc]

    def test_meta_default_is_independent(self):
        """Each Step gets its own meta dict, not a shared default."""
        a = Step(name="a", run="echo")
        b = Step(name="b", run="echo")
        assert a.meta is not b.meta


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

class TestJob:
    def _step(self, name="run"):
        return Step(name=name, run="echo hi")

    def test_minimal(self):
        j = Job(name="build", steps=[self._step()])
        assert j.name == "build"
        assert j.needs == []
        assert j.inputs == []
        assert j.env == {}
        assert j.requires == []
        assert j.secrets == []
        assert j.paths is None
        assert j.diff_enabled is True

    def test_cache_fields_defaults(self):
        """Cache fields are proper dataclass fields with sensible defaults."""
        j = Job(name="build", steps=[self._step()])
        assert j.cache_dirs == []
        assert j.cache_enabled is True
        assert j.cache_skip_on_hit is False
        assert j.cache_keep == 3

    def test_cache_fields_explicit(self):
        j = Job(
            name="test",
            steps=[self._step()],
            cache_dirs=[".venv", "~/.cache/pip"],
            cache_enabled=True,
            cache_skip_on_hit=True,
            cache_keep=5,
        )
        assert j.cache_dirs == [".venv", "~/.cache/pip"]
        assert j.cache_skip_on_hit is True
        assert j.cache_keep == 5

    def test_secrets_field(self):
        j = Job(name="deploy", steps=[self._step()], secrets=["API_KEY", "DB_URL"])
        assert j.secrets == ["API_KEY", "DB_URL"]

    def test_dependency_alias(self):
        """job.dependency is a backwards-compatible alias for job.needs."""
        j = Job(name="b", steps=[self._step()], needs=["a"])
        assert j.dependency == ["a"]

    def test_dependency_setter(self):
        j = Job(name="b", steps=[self._step()])
        j.dependency = ["a", "c"]
        assert j.needs == ["a", "c"]

    def test_mutable(self):
        """Job is mutable (not frozen)."""
        j = Job(name="x", steps=[self._step()])
        j.name = "y"
        assert j.name == "y"
