# git.py
# Small, focused wrapper around the Git CLI.
# This module centralizes all Git interactions so the rest of the codebase
# never needs to call subprocess("git ...") directly.

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


def _git(args: list[str], cwd: Optional[str] = None) -> str:
    """
    Execute a git command and return its stdout as a clean string.

    This is the single low-level entry point for all Git operations in this file.
    Every other function builds on top of this to ensure:
    - consistent invocation of git
    - consistent text output (not bytes)
    - minimal parsing logic duplicated elsewhere

    Args:
        args: List of git arguments (e.g. ["status", "--porcelain"])
        cwd: Optional working directory in which to run the git command.
             Useful if the caller is not already inside the repo.

    Returns:
        Stdout from the git command with surrounding whitespace removed.
    """
    # subprocess.check_output runs the command and captures stdout.
    # If git exits with a non-zero status, an exception will be raised,
    # which is usually desirable for CI / tooling.
    out = subprocess.check_output(
        ["git", *args],
        cwd=cwd,
        text=True,   # return output as str instead of bytes
    )

    # Strip trailing newlines so callers can do clean string comparisons
    return out.strip()


def repo_root() -> Path:
    """
    Return the absolute path to the root of the current Git repository.

    This uses git itself as the source of truth rather than guessing based
    on filesystem layout.

    Returns:
        Path object pointing to the repository root directory.
    """
    # `git rev-parse --show-toplevel` prints the repo root directory
    # regardless of where the command is run from inside the repo.
    return Path(_git(["rev-parse", "--show-toplevel"]))


def head_sha() -> str:
    """
    Return the full SHA hash of the current HEAD commit.

    This is useful for:
    - tagging builds
    - cache keys
    - reproducibility / provenance
    - detecting whether outputs correspond to a specific commit

    Returns:
        Full commit SHA as a string.
    """
    # `git rev-parse HEAD` resolves HEAD to its commit hash
    return _git(["rev-parse", "HEAD"])

    #TODO: when in the future the user wants to run ci on a specific commit they can use the full sha to do so 


def is_dirty() -> bool:
    """
    Check whether the working tree has uncommitted changes.

    This includes:
    - modified files
    - staged files
    - untracked files

    Returns:
        True if the repository is dirty, False if clean.
    """
    # `git status --porcelain` produces stable, machine-readable output.
    # Any output at all indicates the working tree is not clean.
    return _git(["status", "--porcelain"]) != ""


def changed_files(base: str, head: str = "HEAD") -> List[str]:
    """
    Return a list of files changed between two Git references.

    File paths are returned relative to the repository root.

    Typical usage:
        base = merge_base("origin/main")
        files = changed_files(base)

    Args:
        base: The base Git ref (commit, branch, or tag).
        head: The head Git ref to compare against (defaults to HEAD).

    Returns:
        List of file paths (relative to repo root) that changed between
        base and head.
    """
    # `git diff --name-only` outputs only file paths, one per line,
    # which is ideal for programmatic consumption.
    out = _git(["diff", "--name-only", f"{base}..{head}"])

    # No output means no file-level changes
    if not out:
        return []

    # Split newline-delimited output into a Python list
    return out.splitlines()


def merge_base(with_ref: str = "origin/main") -> str:
    """
    Return the merge-base (common ancestor) between HEAD and another ref.

    The merge-base represents the point where the current branch diverged
    from the given reference.

    This is the canonical starting point for:
    - determining what changed in a feature branch
    - selective CI execution
    - monorepo build optimization

    Args:
        with_ref: The reference to compare HEAD against
                  (defaults to origin/main).

    Returns:
        Commit SHA of the merge-base.
    """
    # `git merge-base` finds the best common ancestor between two refs
    return _git(["merge-base", "HEAD", with_ref])
