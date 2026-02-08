# betterci_workflow.py
# Simple workflow to demonstrate git-diff and caching
#
# DEMO:
# 1. CACHE: Run twice - second run uses cache (cache_skip_on_hit=True)
# 2. GIT-DIFF: Run with --git-diff, modify src/ or README.md to see job selection
#
from __future__ import annotations
from betterci.dsl import wf, job, sh

def workflow():
    return wf(
        # Cache demo - creates artifact, cached on second run
        job(
            "cache-demo",
            sh("Create artifact", "mkdir -p .betterci/demo && echo 'Created at:' $(date) > .betterci/demo/artifact.txt"),
            sh("Show artifact", "cat .betterci/demo/artifact.txt"),
            paths=[".betterci/demo/**"],
            inputs=[".betterci/demo/**"],
            cache_dirs=[".betterci/demo"],
            cache_enabled=True,
            cache_skip_on_hit=True,
            cache_keep=5,
        ),

        # Git-diff demo - only runs when src/ files change
        job(
            "check-src",
            sh("Check src files", "echo 'Running because src/ files changed'"),
            paths=["src/**"],
            inputs=["src/**"],
        ),

        # Git-diff demo - only runs when README changes
        job(
            "check-docs",
            sh("Check docs", "echo 'Running because README.md changed'"),
            paths=["README.md"],
            inputs=["README.md"],
        ),
    )

