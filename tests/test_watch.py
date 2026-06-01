"""Tests for filesystem watcher helpers."""

from pathlib import Path

import pytest
import watchfiles

from kdrift import watch


@pytest.mark.unit
class TestKustomizeFilter:
    def test_accepts_yaml(self):
        assert watch._kustomize_filter(watchfiles.Change.modified, "/repo/k8s/dev/patch.yaml")

    def test_accepts_yml(self):
        assert watch._kustomize_filter(watchfiles.Change.modified, "/repo/k8s/dev/patch.yml")

    def test_accepts_json(self):
        assert watch._kustomize_filter(watchfiles.Change.modified, "/repo/config.json")

    def test_accepts_properties(self):
        assert watch._kustomize_filter(watchfiles.Change.modified, "/repo/app.properties")

    def test_rejects_hidden_files(self):
        assert not watch._kustomize_filter(watchfiles.Change.modified, "/repo/.git/HEAD")

    def test_rejects_python_files(self):
        assert not watch._kustomize_filter(watchfiles.Change.modified, "/repo/script.py")

    def test_rejects_markdown(self):
        assert not watch._kustomize_filter(watchfiles.Change.modified, "/repo/README.md")


@pytest.mark.unit
class TestExtractChangedFiles:
    def test_extracts_relative_paths(self, tmp_path):
        sub = tmp_path / "k8s" / "dev"
        sub.mkdir(parents=True)
        changes = {
            (watchfiles.Change.modified, str(sub / "patch.yaml")),
        }
        result = watch._extract_changed_files(changes, tmp_path)
        assert result == [Path("k8s/dev/patch.yaml")]

    def test_skips_files_outside_repo(self, tmp_path):
        changes = {
            (watchfiles.Change.modified, "/some/other/path/file.yaml"),
        }
        result = watch._extract_changed_files(changes, tmp_path)
        assert result == []
