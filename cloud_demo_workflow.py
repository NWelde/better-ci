# cloud_demo_workflow.py
from __future__ import annotations

from betterci.dsl import wf, job, sh

def workflow():
    return wf(
        # 1) Cloud proof: shows the machine actually executing the job
        job(
            "cloud-proof",
            sh("Where am I", "pwd"),
            sh("Who am I (hostname)", "hostname"),
            sh("What time is it", "date"),
            sh("Repo files", "ls -la"),
            diff_enabled=False,
        ),

        # 2) Cache proof: first run writes artifact, second run restores + SKIPS
        job(
            "cache-demo",
            sh("Make demo dir", "mkdir -p .betterci/demo_cache"),
            sh("Write artifact", "echo artifact-v1 > .betterci/demo_cache/artifact.txt"),
            sh("Show artifact", "cat .betterci/demo_cache/artifact.txt"),
            diff_enabled=False,
            cache_dirs=[".betterci/demo_cache"],
            cache_enabled=True,
            cache_skip_on_hit=True,   # key part for 1-minute demo
            cache_keep=5,
        ),

        # 3) DAG proof: only runs after cache-demo (and still works on cache hit)
        job(
            "depends-demo",
            sh("Read artifact", "cat .betterci/demo_cache/artifact.txt"),
            sh("Dependency OK", "echo dependency-worked"),
            needs=["cache-demo"],
            diff_enabled=False,
        ),
    )
