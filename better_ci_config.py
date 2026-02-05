from src.dsl import JobBuilder

def get_jobs():
    lint = (
        JobBuilder("lint") #name of job
        .requires("python") #requires python
        .step("run lint", "ruff .") #steps/ commands to be run
        .with_inputs("src/**", "pyproject.toml")
        .build()
    )

    test = (
        JobBuilder("test")
        .depends_on("lint")
        .requires("python", "pytest")
        .step("run tests", "pytest")
        .with_inputs("src/**", "tests/**")
        .build()
    )

    return [lint, test]
