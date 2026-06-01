"""Tests for kustomize build orchestration."""

from pathlib import Path

import pytest

from kdrift import render


@pytest.mark.unit
class TestCacheKey:
    def test_different_refs_produce_different_keys(self):
        key1 = render.cache_key("abc123", Path("k8s/dev"), ["--enable-helm"], "v5.0.0")
        key2 = render.cache_key("def456", Path("k8s/dev"), ["--enable-helm"], "v5.0.0")
        assert key1 != key2

    def test_different_overlays_produce_different_keys(self):
        key1 = render.cache_key("abc123", Path("k8s/dev"), ["--enable-helm"], "v5.0.0")
        key2 = render.cache_key("abc123", Path("k8s/prod"), ["--enable-helm"], "v5.0.0")
        assert key1 != key2

    def test_different_args_produce_different_keys(self):
        key1 = render.cache_key("abc123", Path("k8s/dev"), ["--enable-helm"], "v5.0.0")
        key2 = render.cache_key("abc123", Path("k8s/dev"), [], "v5.0.0")
        assert key1 != key2

    def test_different_versions_produce_different_keys(self):
        key1 = render.cache_key("abc123", Path("k8s/dev"), ["--enable-helm"], "v5.0.0")
        key2 = render.cache_key("abc123", Path("k8s/dev"), ["--enable-helm"], "v5.1.0")
        assert key1 != key2

    def test_deterministic(self):
        key1 = render.cache_key("abc123", Path("k8s/dev"), ["--enable-helm"], "v5.0.0")
        key2 = render.cache_key("abc123", Path("k8s/dev"), ["--enable-helm"], "v5.0.0")
        assert key1 == key2


@pytest.mark.unit
class TestCacheOperations:
    def test_cache_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        render.set_cached_render("test-key", "rendered yaml content")
        result = render.get_cached_render("test-key")
        assert result == "rendered yaml content"

    def test_cache_miss(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        result = render.get_cached_render("nonexistent-key")
        assert result is None


@pytest.mark.unit
class TestFindKustomize:
    def test_kustomize_not_on_path(self, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        with pytest.raises(render.RenderError, match="not found"):
            render.find_kustomize()


@pytest.mark.unit
class TestKustomizeVersion:
    def test_returns_unknown_for_missing_binary(self):
        result = render.kustomize_version("/nonexistent/kustomize")
        assert result == "unknown"


@pytest.mark.unit
class TestRenderOverlay:
    def test_kustomize_not_found(self, tmp_path):
        result = render.render_overlay(tmp_path, tmp_path, binary="/nonexistent/kustomize")
        assert not result.success
        assert result.exit_code == 127

    def test_build_failure_captures_stderr(self, tmp_path):
        script = tmp_path / "fake-kustomize"
        script.write_text("#!/bin/sh\necho 'Error: bad yaml' >&2\nexit 1\n")
        script.chmod(0o755)
        result = render.render_overlay(Path("k8s/dev"), tmp_path, binary=str(script))
        assert not result.success
        assert "bad yaml" in (result.error or "")


@pytest.mark.unit
class TestRenderOverlaysParallel:
    def test_empty_list(self, tmp_path):
        results = render.render_overlays_parallel([], tmp_path)
        assert results == []
