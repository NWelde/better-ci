"""Tests for betterci.cache — key computation and artifact store."""
import json
import tarfile
from pathlib import Path

import pytest

from betterci.model import Job, Step
from betterci.dsl import job, sh
from betterci.cache import CacheStore, compute_job_cache_key, CacheHit


def _make_job(name="test", **kwargs) -> Job:
    return job(name, sh("run", "pytest -q"), **kwargs)


# ---------------------------------------------------------------------------
# Cache key computation
# ---------------------------------------------------------------------------

class TestComputeCacheKey:
    def test_deterministic(self, tmp_path):
        j = _make_job()
        k1, _ = compute_job_cache_key(j, repo_root=tmp_path)
        k2, _ = compute_job_cache_key(j, repo_root=tmp_path)
        assert k1 == k2

    def test_different_names_produce_different_keys(self, tmp_path):
        k1, _ = compute_job_cache_key(_make_job("a"), repo_root=tmp_path)
        k2, _ = compute_job_cache_key(_make_job("b"), repo_root=tmp_path)
        assert k1 != k2

    def test_different_commands_produce_different_keys(self, tmp_path):
        j1 = job("x", sh("r", "pytest -q"))
        j2 = job("x", sh("r", "pytest -v"))
        k1, _ = compute_job_cache_key(j1, repo_root=tmp_path)
        k2, _ = compute_job_cache_key(j2, repo_root=tmp_path)
        assert k1 != k2

    def test_different_env_produce_different_keys(self, tmp_path):
        j1 = _make_job(env={"NODE_ENV": "test"})
        j2 = _make_job(env={"NODE_ENV": "production"})
        k1, _ = compute_job_cache_key(j1, repo_root=tmp_path)
        k2, _ = compute_job_cache_key(j2, repo_root=tmp_path)
        assert k1 != k2

    def test_input_file_changes_key(self, tmp_path):
        f = tmp_path / "pyproject.toml"
        f.write_text("version = '1.0'")
        j = job("x", sh("r", "pytest"), inputs=["pyproject.toml"])

        k1, _ = compute_job_cache_key(j, repo_root=tmp_path)

        f.write_text("version = '2.0'")
        k2, _ = compute_job_cache_key(j, repo_root=tmp_path)

        assert k1 != k2

    def test_manifest_contains_key(self, tmp_path):
        j = _make_job()
        key, manifest = compute_job_cache_key(j, repo_root=tmp_path)
        assert manifest["key"] == key
        assert "payload" in manifest

    def test_key_is_hex_string(self, tmp_path):
        k, _ = compute_job_cache_key(_make_job(), repo_root=tmp_path)
        assert len(k) == 64
        int(k, 16)  # should not raise


# ---------------------------------------------------------------------------
# CacheStore: save & restore
# ---------------------------------------------------------------------------

class TestCacheStore:
    def _job_with_cache(self, cache_dir_name="mydir", **kwargs) -> Job:
        return job("test", sh("run", "echo ok"),
                   cache_dirs=[cache_dir_name], **kwargs)

    def test_restore_miss_on_empty_store(self, tmp_path):
        store = CacheStore(tmp_path / "cache")
        j = self._job_with_cache()
        hit = store.restore(j, repo_root=tmp_path)
        assert hit.hit is False
        assert "miss" in hit.reason

    def test_save_creates_archive(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / "mydir"
        target.mkdir()
        (target / "result.txt").write_text("built!")

        j = self._job_with_cache()
        store = CacheStore(tmp_path / "cache")
        key, manifest = store.save(j, repo_root=repo)

        archive = store.artifact_path(j.name, key)
        assert archive.exists()
        assert key == manifest["key"]

    def test_save_then_restore(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / "mydir"
        target.mkdir()
        (target / "output.txt").write_text("artifact!")

        j = self._job_with_cache()
        store = CacheStore(tmp_path / "cache")
        key, _ = store.save(j, repo_root=repo)

        # Remove the directory to confirm restore actually puts it back
        import shutil
        shutil.rmtree(target)
        assert not target.exists()

        hit = store.restore(j, repo_root=repo)
        assert hit.hit is True
        assert (target / "output.txt").read_text() == "artifact!"

    def test_cache_disabled_skips_restore(self, tmp_path):
        j = job("x", sh("r", "echo"), cache_dirs=["d"], cache_enabled=False)
        store = CacheStore(tmp_path)
        hit = store.restore(j, repo_root=tmp_path)
        assert hit.hit is False
        assert "disabled" in hit.reason

    def test_no_cache_dirs_skips_restore(self, tmp_path):
        j = job("x", sh("r", "echo"))  # cache_dirs=[] by default
        store = CacheStore(tmp_path)
        hit = store.restore(j, repo_root=tmp_path)
        assert hit.hit is False
        assert "no cache_dirs" in hit.reason

    def test_prune_keeps_newest(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mydir").mkdir()
        (repo / "mydir" / "f.txt").write_text("hi")

        j = self._job_with_cache()
        store = CacheStore(tmp_path / "cache")

        # Save 4 times with slightly different env to get different keys
        for i in range(4):
            ji = job("test", sh("run", f"echo {i}"), cache_dirs=["mydir"])
            store.save(ji, repo_root=repo)

        job_dir = store._job_dir("test")
        archives_before = list(job_dir.glob("*.tar.gz"))
        assert len(archives_before) == 4

        store.prune("test", keep=2)
        archives_after = list(job_dir.glob("*.tar.gz"))
        assert len(archives_after) == 2
