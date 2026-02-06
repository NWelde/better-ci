# cache.py
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from model import Job

# ---------------------------------------------------------------------
# Core idea
# ---------------------------------------------------------------------
# Job-level caching:
#   cache_key = hash(
#       job.name,
#       step commands + cwd,
#       job.env,
#       tool versions,
#       contents of declared input files/dirs (globs),
#       optional salt (e.g., python version)
#   )
#
# Cache artifact:
#   a tar.gz containing declared "cache_dirs" (outputs / install dirs / build dirs)
#   plus a manifest.json for explainability.
#
# By design, this file uses only stdlib.
#
# Required job fields (you already have): name, steps, env, requires, inputs
# Optional job fields supported via getattr(job, ...):
#   - cache_enabled: bool (default True)
#   - cache_dirs: List[str] (dirs/files to STORE/RESTORE)
#   - cache_exclude: List[str] (glob patterns to exclude inside each cache dir)
#   - tool_versions: Dict[str, str] (override autodetect)
#   - cache_key_extra: Dict[str, str] (arbitrary extra salt)
#
# Example usage in runner (high-level):
#   store = CacheStore(".betterci/cache")
#   hit, key, why = store.restore(job)
#   if not hit:
#       run_job(job)
#       store.save(job, key, why)
#
# ---------------------------------------------------------------------


DEFAULT_CACHE_DIR = ".betterci/cache"
DEFAULT_CACHE_EXCLUDES = [
    ".git/**",
    ".betterci/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.DS_Store",
]


@dataclass(frozen=True)
class CacheHit:
    hit: bool
    key: str
    reason: str  # human readable
    manifest: Dict


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _sha256_str(s: str) -> str:
    return _sha256_bytes(s.encode("utf-8"))


def _json_dumps_stable(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _relpath(p: Path, root: Path) -> str:
    return str(p.resolve().relative_to(root.resolve())).replace("\\", "/")


def _iter_files_under(root: Path) -> Iterable[Path]:
    # deterministic traversal
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def _matches_any_glob(rel: str, globs: List[str]) -> bool:
    # Simple glob matcher via fnmatch-like behavior using Path.match.
    # NOTE: Path.match treats patterns as path-like globs, good enough for hackathon.
    rel_path = Path(rel)
    for g in globs:
        try:
            if rel_path.match(g):
                return True
        except Exception:
            # if a weird pattern is passed, ignore it rather than breaking cache
            continue
    return False


def _hash_file_contents(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _resolve_globs(repo_root: Path, patterns: List[str]) -> List[Path]:
    """
    Expand job.inputs patterns into concrete paths.
    Supports:
      - file path: "pyproject.toml"
      - dir path:  "src/"
      - glob:      "backend/**", "tests/**/*.py"
    """
    out: List[Path] = []
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        p = (repo_root / pat)
        if p.exists():
            out.append(p)
            continue

        # Glob relative to repo root
        # Use rglob if pattern contains **, else glob
        try:
            matches = sorted(repo_root.glob(pat))
        except Exception:
            matches = []
        out.extend([m for m in matches if m.exists()])

    # De-dupe while preserving order
    seen = set()
    uniq: List[Path] = []
    for p in out:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def _tool_version(tool: str) -> Optional[str]:
    """
    Best-effort version discovery. Keep it simple and stable.
    """
    import subprocess

    # Fast paths / known flags
    candidates = [
        [tool, "--version"],
        [tool, "-V"],
        [tool, "version"],
    ]
    for cmd in candidates:
        try:
            completed = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                check=False,
            )
            out = (completed.stdout or "").strip()
            err = (completed.stderr or "").strip()
            text = out if out else err
            if completed.returncode == 0 and text:
                # Normalize whitespace to make hashing stable
                return " ".join(text.split())
        except Exception:
            continue
    return None


def _hash_inputs(
    repo_root: Path,
    inputs: List[str],
    *,
    excludes: List[str],
) -> Tuple[str, Dict]:
    """
    Hash the declared input set deterministically:
      - include file contents
      - include file relative paths
      - include file size (optional, but can help detect weird reads)
    """
    resolved = _resolve_globs(repo_root, inputs)

    file_fps: List[Tuple[str, str, int]] = []
    missing: List[str] = []

    for p in resolved:
        if not p.exists():
            missing.append(str(p))
            continue

        if p.is_file():
            rel = _relpath(p, repo_root)
            if _matches_any_glob(rel, excludes):
                continue
            digest = _hash_file_contents(p)
            file_fps.append((rel, digest, p.stat().st_size))
            continue

        if p.is_dir():
            for f in _iter_files_under(p):
                rel = _relpath(f, repo_root)
                if _matches_any_glob(rel, excludes):
                    continue
                digest = _hash_file_contents(f)
                file_fps.append((rel, digest, f.stat().st_size))
            continue

    file_fps.sort(key=lambda t: t[0])  # stable ordering by relpath
    payload = {"files": file_fps, "missing": sorted(missing)}
    return _sha256_str(_json_dumps_stable(payload)), payload


def compute_job_cache_key(
    job: Job,
    *,
    repo_root: str | Path = ".",
    excludes: Optional[List[str]] = None,
) -> Tuple[str, Dict]:
    """
    Returns (cache_key, manifest_bits) where manifest_bits can be stored for explainability.
    """
    root = Path(repo_root).resolve()
    exclude_globs = list(DEFAULT_CACHE_EXCLUDES)
    if excludes:
        exclude_globs.extend(excludes)

    # Steps fingerprint
    steps = []
    for s in getattr(job, "steps", []) or []:
        steps.append(
            {
                "name": getattr(s, "name", "<unnamed-step>"),
                "run": getattr(s, "run", ""),
                "cwd": getattr(s, "cwd", None) or ".",
            }
        )

    # Env fingerprint (stable)
    env = dict(getattr(job, "env", {}) or {})

    # Tool versions
    requires = list(getattr(job, "requires", []) or [])
    tool_versions_override = getattr(job, "tool_versions", None)
    tool_versions: Dict[str, Optional[str]] = {}
    if isinstance(tool_versions_override, dict):
        # user can pin their own stable values
        for t in requires:
            tool_versions[t] = tool_versions_override.get(t)
    else:
        for t in requires:
            tool_versions[t] = _tool_version(t)

    # Inputs hashing (this is the BIG thing)
    inputs = list(getattr(job, "inputs", []) or [])
    inputs_hash, inputs_manifest = _hash_inputs(root, inputs, excludes=exclude_globs)

    # Extra salt (optional)
    cache_key_extra = getattr(job, "cache_key_extra", None)
    if not isinstance(cache_key_extra, dict):
        cache_key_extra = {}

    payload = {
        "v": 1,  # bump this if you change hashing format
        "job": job.name,
        "steps": steps,
        "env": env,
        "requires": requires,
        "tool_versions": tool_versions,
        "inputs_hash": inputs_hash,
        "cache_key_extra": cache_key_extra,
    }

    key = _sha256_str(_json_dumps_stable(payload))
    manifest = {
        "key": key,
        "payload": payload,
        "inputs": inputs_manifest,
        "excludes": exclude_globs,
        "generated_at_unix": int(time.time()),
    }
    return key, manifest


def _tar_add_path(
    tar: tarfile.TarFile,
    repo_root: Path,
    src: Path,
    arc_prefix: str,
    *,
    exclude_globs: List[str],
) -> None:
    """
    Add src (file/dir) into tar under arc_prefix, skipping excluded paths.
    """
    src = src.resolve()
    if not src.exists():
        return

    if src.is_file():
        rel = _relpath(src, repo_root)
        if _matches_any_glob(rel, exclude_globs):
            return
        arcname = str(Path(arc_prefix) / rel).replace("\\", "/")
        tar.add(str(src), arcname=arcname, recursive=False)
        return

    # directory
    for f in _iter_files_under(src):
        rel = _relpath(f, repo_root)
        if _matches_any_glob(rel, exclude_globs):
            continue
        arcname = str(Path(arc_prefix) / rel).replace("\\", "/")
        tar.add(str(f), arcname=arcname, recursive=False)


class CacheStore:
    """
    File-based cache store:
      root/
        <job_name>/
          <key>.tar.gz
          <key>.manifest.json
    """

    def __init__(self, root: str | Path = DEFAULT_CACHE_DIR):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_name: str) -> Path:
        d = self.root / job_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def artifact_path(self, job_name: str, key: str) -> Path:
        return self._job_dir(job_name) / f"{key}.tar.gz"

    def manifest_path(self, job_name: str, key: str) -> Path:
        return self._job_dir(job_name) / f"{key}.manifest.json"

    def restore(self, job: Job, *, repo_root: str | Path = ".") -> CacheHit:
        """
        Restore cached dirs/files into the working directory.
        Returns CacheHit with explainability manifest.

        NOTE:
          - restore is "overwrite by extraction". If you need cleaning, do it before restore.
          - only restores job.cache_dirs.
        """
        if getattr(job, "cache_enabled", True) is False:
            return CacheHit(hit=False, key="", reason="cache disabled for job", manifest={})

        root = Path(repo_root).resolve()
        cache_dirs = list(getattr(job, "cache_dirs", []) or [])
        if not cache_dirs:
            return CacheHit(hit=False, key="", reason="no cache_dirs specified", manifest={})

        key, manifest = compute_job_cache_key(job, repo_root=root)

        art = self.artifact_path(job.name, key)
        man = self.manifest_path(job.name, key)

        if not art.exists() or not man.exists():
            return CacheHit(hit=False, key=key, reason="cache miss", manifest=manifest)

        # Extract into repo root
        try:
            with tarfile.open(str(art), mode="r:gz") as tar:
                tar.extractall(path=str(root))
        except Exception as e:
            return CacheHit(hit=False, key=key, reason=f"cache exists but restore failed: {e}", manifest=manifest)

        # Load stored manifest (what was actually saved)
        try:
            stored = json.loads(man.read_text(encoding="utf-8"))
        except Exception:
            stored = {}

        return CacheHit(hit=True, key=key, reason="cache hit: restored artifact", manifest=stored or manifest)

    def save(
        self,
        job: Job,
        key: Optional[str] = None,
        manifest: Optional[Dict] = None,
        *,
        repo_root: str | Path = ".",
    ) -> Tuple[str, Dict]:
        """
        Save cache_dirs into artifact for this job key.
        Returns (key, manifest).

        Safe behavior:
          - if cache_dirs missing -> no-op but still returns computed key/manifest
          - excludes DEFAULT_CACHE_EXCLUDES + job.cache_exclude
        """
        if getattr(job, "cache_enabled", True) is False:
            # Still compute key for explainability consistency
            k, m = compute_job_cache_key(job, repo_root=repo_root)
            return k, m

        root = Path(repo_root).resolve()
        cache_dirs = list(getattr(job, "cache_dirs", []) or [])
        if not cache_dirs:
            k, m = compute_job_cache_key(job, repo_root=root)
            return k, m

        if key is None or manifest is None:
            key, manifest = compute_job_cache_key(job, repo_root=root)

        exclude_globs = list(DEFAULT_CACHE_EXCLUDES)
        job_ex = getattr(job, "cache_exclude", None)
        if isinstance(job_ex, list):
            exclude_globs.extend([str(x) for x in job_ex])

        art = self.artifact_path(job.name, key)
        man = self.manifest_path(job.name, key)

        tmp = art.with_suffix(".tar.gz.tmp")
        try:
            # Build tar.gz in tmp, then atomic rename
            with tarfile.open(str(tmp), mode="w:gz") as tar:
                for entry in cache_dirs:
                    src = (root / entry).resolve()
                    # Save under arc_prefix="" so paths extract back correctly (by relpath)
                    _tar_add_path(tar, root, src, arc_prefix="", exclude_globs=exclude_globs)

                # Always include the manifest inside the tar too (nice for portability)
                payload = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
                info = tarfile.TarInfo(name=f".betterci_cache_manifest/{job.name}/{key}.manifest.json")
                info.size = len(payload)
                info.mtime = int(time.time())
                tar.addfile(info, fileobj=_BytesIO(payload))

            tmp.replace(art)
            man.write_text(json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False), encoding="utf-8")
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        return key, manifest

    def prune(self, job_name: str, keep: int = 3) -> None:
        """
        Keep only the newest N artifacts for a job.
        Uses file mtime as "newest".
        """
        d = self._job_dir(job_name)
        tars = sorted(d.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in tars[keep:]:
            key = p.stem.replace(".tar", "")
            man = d / f"{key}.manifest.json"
            p.unlink(missing_ok=True)
            man.unlink(missing_ok=True)


# Small helper for tar addfile without importing io everywhere
class _BytesIO:
    def __init__(self, b: bytes):
        import io

        self._bio = io.BytesIO(b)

    def read(self, n: int = -1) -> bytes:
        return self._bio.read(n)

    def seek(self, pos: int, whence: int = 0) -> int:
        return self._bio.seek(pos, whence)

    def tell(self) -> int:
        return self._bio.tell()


# ---------------------------------------------------------------------
# Convenience helpers (optional)
# ---------------------------------------------------------------------

def ensure_clean_dir(path: str | Path) -> None:
    p = Path(path)
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)


def cacheable_job_defaults(job: Job) -> None:
    """
    Optional mutator you can call in your DSL:
      - if you forget cache_dirs, default to [] (no caching)
      - if you forget inputs, default to []
    """
    if getattr(job, "inputs", None) is None:
        job.inputs = []
    if getattr(job, "cache_dirs", None) is None:
        setattr(job, "cache_dirs", [])
    if getattr(job, "cache_enabled", None) is None:
        setattr(job, "cache_enabled", True)
