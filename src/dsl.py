# dsl.py
from model import Step, Job


class JobBuilder:
    def __init__(self, name: str):
        self.name = name
        self.dependency: list[str] = []
        self.steps: list[Step] = []
        self.inputs: list[str] = []
        self.env: dict[str, str] = {}
        self.requires: list[str] = []

    def depends_on(self, *job_names: str):
        self.dependency.extend(job_names)
        return self
    
    def define_requirments(self, *tools: str ):
        self.requires.extend(tools)
        return self

    def define_step(self, name: str, run: str, cwd: str | None = None):
        self.steps.append(Step(name=name, run=run, cwd=cwd))
        return self

    def with_inputs(self, *paths: str):
        self.inputs.extend(paths)
        return self

    def with_env(self, **env):
        self.env.update(env)
        return self

    def build(self) -> Job:
        if not self.steps:
            raise ValueError(f"Job '{self.name}' has no steps")

        return Job(
            name=self.name,
            dependency =self.dependency,  
            steps=self.steps,
            inputs=self.inputs,
            env=self.env,
        )


