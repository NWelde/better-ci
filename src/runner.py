import shutil
import os
from dsl import define_step #format define_step(name, run , cwd)
from dsl import define_requirements #format define_requirements(tools)
from better_ci_config import get_jobs

job = get_jobs() #The user defined jobs in ther better_ci_config.py

def package_check_all(jobs):
    missing = []
    for job in jobs:
        for tool in job.requires:
            if shutil.which(tool) is None:
                missing.append((job.name, tool))

    if missing:
        lines = ["Missing required tools:"]
        for job_name, tool in missing:
            hint = TOOL_HINTS.get(tool, "Install this tool and ensure it is on PATH.")
            lines.append(f"- {tool} (needed by job '{job_name}') → {hint}")
        lines.append(f"PATH: {os.environ.get('PATH', '(not set)')}")
        raise RuntimeError("\n".join(lines))


