"""Tests for betterci.step_workflows.artifacts — artifact save/load."""
import pytest
from pathlib import Path

from betterci.step_workflows.artifacts import ArtifactStore


class TestArtifactStore:
    def test_save_file(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "dist.whl").write_text("wheel contents")

        store = ArtifactStore(tmp_path / "artifacts")
        archive = store.save("dist", "dist.whl", repo_root=repo)

        assert archive.exists()
        assert archive.suffix == ".gz"

    def test_save_directory(self, tmp_path):
        repo = tmp_path / "repo"
        dist = repo / "dist"
        dist.mkdir(parents=True)
        (dist / "pkg-1.0.whl").write_text("wheel")
        (dist / "pkg-1.0.tar.gz").write_text("sdist")

        store = ArtifactStore(tmp_path / "artifacts")
        archive = store.save("dist", "dist", repo_root=repo)
        assert archive.exists()

    def test_load_restores_file(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        src = repo / "output.txt"
        src.write_text("hello artifact")

        store = ArtifactStore(tmp_path / "artifacts")
        store.save("output", "output.txt", repo_root=repo)

        # Remove source to verify load actually restores it
        src.unlink()

        dest = tmp_path / "restore"
        store.load("output", dest, repo_root=tmp_path)
        assert (dest / "output.txt").read_text() == "hello artifact"

    def test_load_missing_raises(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        with pytest.raises(FileNotFoundError, match="No artifact found"):
            store.load("nonexistent", tmp_path, repo_root=tmp_path)

    def test_save_nonexistent_source_raises(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        with pytest.raises(FileNotFoundError, match="source not found"):
            store.save("x", "does_not_exist.txt", repo_root=tmp_path)

    def test_list_artifacts(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.txt").write_text("a")
        (repo / "b.txt").write_text("b")

        store = ArtifactStore(tmp_path / "artifacts")
        store.save("alpha", "a.txt", repo_root=repo)
        store.save("beta", "b.txt", repo_root=repo)

        names = set(store.list_artifacts())
        assert "alpha" in names
        assert "beta" in names

    def test_load_returns_most_recent(self, tmp_path):
        """When saved twice, load should return the second (most recent) version."""
        repo = tmp_path / "repo"
        repo.mkdir()
        f = repo / "result.txt"

        store = ArtifactStore(tmp_path / "artifacts")

        f.write_text("version 1")
        store.save("result", "result.txt", repo_root=repo)

        import time; time.sleep(0.01)  # ensure different mtime

        f.write_text("version 2")
        store.save("result", "result.txt", repo_root=repo)

        dest = tmp_path / "out"
        store.load("result", dest, repo_root=tmp_path)
        assert (dest / "result.txt").read_text() == "version 2"

    def test_meta_json_written(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "x.txt").write_text("data")

        store = ArtifactStore(tmp_path / "artifacts")
        store.save("x", "x.txt", repo_root=repo)

        art_dir = store._artifact_dir("x")
        versions = [d for d in art_dir.iterdir() if d.is_dir() and d.name != "latest"]
        assert len(versions) == 1
        meta_file = versions[0] / "meta.json"
        assert meta_file.exists()

        import json
        meta = json.loads(meta_file.read_text())
        assert meta["name"] == "x"
        assert "created_at" in meta
        assert "size_bytes" in meta
