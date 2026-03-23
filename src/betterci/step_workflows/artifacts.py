# step_workflows/artifacts.py
"""
Artifact management for BetterCI.

Artifacts are named outputs produced by a job that can be:
  - saved to .betterci/artifacts/<run_id>/<job_name>/<artifact_name>/
  - loaded by downstream jobs that declare them as inputs

This gives BetterCI DAG-aware output passing without a plugin system:
jobs declare what they produce, downstream jobs declare what they consume.

Usage in a workflow file:

    from betterci import wf, job, sh
    from betterci.step_workflows.artifacts import artifact_step, use_artifact

    def workflow():
        return wf(
            job(
                "build",
                sh("compile", "python -m build"),
                sh("publish artifact", artifact_step("dist", "dist/")),
            ),
            job(
                "publish",
                sh("load artifact", use_artifact("dist", dest="dist/")),
                sh("upload", "twine upload dist/*"),
                needs=["build"],
            ),
        )
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Optional


# Default artifact root (relative to repo root).
DEFAULT_ARTIFACT_ROOT = ".betterci/artifacts"


# ---------------------------------------------------------------------------
# DSL helpers — generate shell commands that call the artifact CLI
# ---------------------------------------------------------------------------

def artifact_step(name: str, source_path: str, *, artifact_root: str = DEFAULT_ARTIFACT_ROOT) -> str:
    """
    Return a shell command that saves source_path as a named artifact.

    Designed to be used inside sh():
        sh("Save dist", artifact_step("dist", "dist/"))
    """
    return (
        f'python -m betterci.step_workflows.artifacts save '
        f'--name "{name}" --source "{source_path}" --root "{artifact_root}"'
    )


def use_artifact(name: str, *, dest: str = ".", artifact_root: str = DEFAULT_ARTIFACT_ROOT) -> str:
    """
    Return a shell command that loads a named artifact into dest.

    Designed to be used inside sh():
        sh("Load dist", use_artifact("dist", dest="dist/"))
    """
    return (
        f'python -m betterci.step_workflows.artifacts load '
        f'--name "{name}" --dest "{dest}" --root "{artifact_root}"'
    )


# ---------------------------------------------------------------------------
# Core artifact operations
# ---------------------------------------------------------------------------

class ArtifactStore:
    """
    File-based artifact store.

    Layout:
        .betterci/artifacts/
            <artifact_name>/
                latest/           <- symlink to most recent archive dir
                <timestamp>/
                    artifact.tar.gz
                    meta.json
    """

    def __init__(self, root: str | Path = DEFAULT_ARTIFACT_ROOT):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _artifact_dir(self, name: str) -> Path:
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, name: str, source: str | Path, *, repo_root: str | Path = ".") -> Path:
        """
        Save source_path as a named artifact.

        Returns the path to the saved archive.
        """
        src = (Path(repo_root) / source).resolve()
        if not src.exists():
            raise FileNotFoundError(
                f"Artifact source not found: {src}\n"
                f"  (artifact name: {name!r}, declared source: {source!r})"
            )

        art_dir = self._artifact_dir(name)
        ts = str(int(time.time()))
        version_dir = art_dir / ts
        version_dir.mkdir(parents=True, exist_ok=True)

        archive = version_dir / "artifact.tar.gz"
        with tarfile.open(str(archive), "w:gz") as tar:
            tar.add(str(src), arcname=src.name)

        meta = {
            "name": name,
            "source": str(source),
            "created_at": ts,
            "size_bytes": archive.stat().st_size,
        }
        (version_dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        # Update "latest" symlink
        latest = art_dir / "latest"
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        try:
            latest.symlink_to(version_dir)
        except (OSError, NotImplementedError):
            pass  # Symlinks not supported on this FS; skip

        return archive

    def load(self, name: str, dest: str | Path, *, repo_root: str | Path = ".") -> None:
        """
        Load the most recent version of a named artifact into dest.

        Raises FileNotFoundError if no artifact with this name exists.
        """
        art_dir = self._artifact_dir(name)

        # Find most recent version by mtime
        versions = sorted(
            [d for d in art_dir.iterdir() if d.is_dir() and d.name != "latest"],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not versions:
            raise FileNotFoundError(
                f"No artifact found with name {name!r}.\n"
                f"  Looked in: {art_dir}\n"
                f"  Make sure the producing job ran successfully before this one."
            )

        archive = versions[0] / "artifact.tar.gz"
        if not archive.exists():
            raise FileNotFoundError(
                f"Artifact archive missing for {name!r}: {archive}"
            )

        dest_path = (Path(repo_root) / dest).resolve()
        dest_path.mkdir(parents=True, exist_ok=True)

        with tarfile.open(str(archive), "r:gz") as tar:
            tar.extractall(path=str(dest_path))

    def list_artifacts(self) -> list[str]:
        """Return the names of all stored artifacts."""
        return [
            d.name
            for d in self.root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]


# ---------------------------------------------------------------------------
# CLI entry point (called via python -m betterci.step_workflows.artifacts)
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="betterci.step_workflows.artifacts")
    sub = parser.add_subparsers(dest="cmd", required=True)

    save_p = sub.add_parser("save", help="Save an artifact")
    save_p.add_argument("--name", required=True)
    save_p.add_argument("--source", required=True)
    save_p.add_argument("--root", default=DEFAULT_ARTIFACT_ROOT)

    load_p = sub.add_parser("load", help="Load an artifact")
    load_p.add_argument("--name", required=True)
    load_p.add_argument("--dest", default=".")
    load_p.add_argument("--root", default=DEFAULT_ARTIFACT_ROOT)

    args = parser.parse_args()
    store = ArtifactStore(root=args.root)

    if args.cmd == "save":
        archive = store.save(args.name, args.source)
        print(f"Artifact saved: {args.name!r} -> {archive}")
    elif args.cmd == "load":
        store.load(args.name, args.dest)
        print(f"Artifact loaded: {args.name!r} -> {args.dest}")


if __name__ == "__main__":
    _main()
