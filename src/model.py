from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    """A single command (step) inside a CI job."""
    name: str
    run: str
    cwd: str | None = None


@dataclass
class Job:
    """A CI job: steps + dependencies + metadata for selection/caching."""
    name: str
    steps: list[Step]
    dependency: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
