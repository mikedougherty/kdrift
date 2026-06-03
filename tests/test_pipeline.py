"""Tests for the diff pipeline orchestration."""

from pathlib import Path
from unittest import mock

import pytest

from kdrift import models, pipeline


def _make_render_ctx() -> pipeline._RenderContext:
    return pipeline._RenderContext(
        repo_root=Path("/repo"),
        args=["--enable-helm"],
        binary="/usr/bin/kustomize",
        kust_ver="v5.0.0",
    )


def _make_overlay(path: str = "k8s/dev") -> models.Overlay:
    return models.Overlay(
        path=Path(path),
        kustomization_file=Path(f"{path}/kustomization.yaml"),
    )


def _make_render_result(
    path: str = "k8s/dev",
    output: str = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test\n",
    success: bool = True,
) -> models.RenderResult:
    if success:
        return models.RenderResult(overlay_path=Path(path), output=output, exit_code=0)
    return models.RenderResult(overlay_path=Path(path), error="build failed", exit_code=1)


def _make_overlay_result(path: str = "k8s/dev", has_changes: bool = False) -> models.OverlayResult:
    changes = []
    if has_changes:
        changes.append(
            models.ResourceChange(
                resource_id=models.ResourceId(kind="ConfigMap", name="test"),
                status=models.DiffStatus.MODIFIED,
                diff_text="- old\n+ new",
            )
        )
    return models.OverlayResult(path=Path(path), changes=changes)


@pytest.mark.unit
class TestRunDiffWorkingTree:
    """Test the default working-tree-vs-ref pipeline path."""

    @mock.patch("kdrift.pipeline.diff")
    @mock.patch("kdrift.pipeline.render")
    @mock.patch("kdrift.pipeline.git")
    @mock.patch("kdrift.pipeline.discover")
    def test_no_changes(self, mock_discover, mock_git, mock_render, mock_diff):
        mock_git.resolve_ref.return_value = "a" * 40
        mock_git.get_short_sha.return_value = "a1b2c3d"
        mock_git.changed_files.return_value = []

        result = pipeline.run_diff(Path("/repo"))

        assert result.ref == "a1b2c3d"
        assert result.target_ref is None
        assert not result.has_changes

    @mock.patch("kdrift.pipeline.diff")
    @mock.patch("kdrift.pipeline.render")
    @mock.patch("kdrift.pipeline.git")
    @mock.patch("kdrift.pipeline.discover")
    def test_with_changes(self, mock_discover, mock_git, mock_render, mock_diff):
        mock_git.resolve_ref.return_value = "a" * 40
        mock_git.get_short_sha.return_value = "a1b2c3d"
        mock_git.changed_files.return_value = [Path("k8s/dev/patch.yaml")]

        overlay = _make_overlay()
        graph = mock.MagicMock()
        graph.affected_overlays.return_value = [overlay]
        mock_discover.DependencyGraph.return_value = graph

        mock_render.DEFAULT_KUSTOMIZE_ARGS = ["--enable-helm"]
        mock_render.find_kustomize.return_value = "/usr/bin/kustomize"
        mock_render.kustomize_version.return_value = "v5.0.0"
        mock_render.render_overlays_parallel.return_value = [_make_render_result()]
        mock_render.cache_key.return_value = "cachekey"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result(output="baseline yaml")

        mock_diff.diff_rendered.return_value = _make_overlay_result(has_changes=True)

        wt_mock = mock.MagicMock()
        wt_mock.__enter__ = mock.MagicMock(return_value=wt_mock)
        wt_mock.__exit__ = mock.MagicMock(return_value=False)
        wt_mock.path = Path("/tmp/wt")
        mock_git.Worktree.return_value = wt_mock

        result = pipeline.run_diff(Path("/repo"))

        assert result.ref == "a1b2c3d"
        assert result.target_ref is None
        assert result.has_changes
        assert len(result.overlays) == 1
        mock_git.changed_files.assert_called_once()

    @mock.patch("kdrift.pipeline.diff")
    @mock.patch("kdrift.pipeline.render")
    @mock.patch("kdrift.pipeline.git")
    @mock.patch("kdrift.pipeline.discover")
    def test_no_affected_overlays(self, mock_discover, mock_git, mock_render, mock_diff):
        mock_git.resolve_ref.return_value = "a" * 40
        mock_git.get_short_sha.return_value = "a1b2c3d"
        mock_git.changed_files.return_value = [Path("README.md")]

        graph = mock.MagicMock()
        graph.affected_overlays.return_value = []
        mock_discover.DependencyGraph.return_value = graph

        mock_render.DEFAULT_KUSTOMIZE_ARGS = ["--enable-helm"]

        result = pipeline.run_diff(Path("/repo"))

        assert not result.has_changes
        assert len(result.overlays) == 0


@pytest.mark.unit
class TestRunDiffRefVsRef:
    """Test the two-ref comparison pipeline path."""

    @mock.patch("kdrift.pipeline.diff")
    @mock.patch("kdrift.pipeline.render")
    @mock.patch("kdrift.pipeline.git")
    @mock.patch("kdrift.pipeline.discover")
    def test_no_changes_between_refs(self, mock_discover, mock_git, mock_render, mock_diff):
        mock_git.resolve_ref.side_effect = lambda r, _: f"sha-{r}"
        mock_git.get_short_sha.side_effect = lambda r, _: f"short-{r}"
        mock_git.changed_files_between.return_value = []

        mock_render.DEFAULT_KUSTOMIZE_ARGS = ["--enable-helm"]

        result = pipeline.run_diff(Path("/repo"), ref="main~5", target_ref="main~2")

        assert result.ref == "short-main~5"
        assert result.target_ref == "short-main~2"
        assert not result.has_changes
        mock_git.changed_files_between.assert_called_once()
        mock_git.changed_files.assert_not_called()

    @mock.patch("kdrift.pipeline.diff")
    @mock.patch("kdrift.pipeline.render")
    @mock.patch("kdrift.pipeline.git")
    @mock.patch("kdrift.pipeline.discover")
    def test_with_changes_between_refs(self, mock_discover, mock_git, mock_render, mock_diff):
        mock_git.resolve_ref.side_effect = lambda r, _: f"sha-{r}"
        mock_git.get_short_sha.side_effect = lambda r, _: f"short-{r}"
        mock_git.changed_files_between.return_value = [Path("k8s/dev/patch.yaml")]

        overlay = _make_overlay()
        graph = mock.MagicMock()
        graph.affected_overlays.return_value = [overlay]
        mock_discover.DependencyGraph.return_value = graph

        mock_render.DEFAULT_KUSTOMIZE_ARGS = ["--enable-helm"]
        mock_render.find_kustomize.return_value = "/usr/bin/kustomize"
        mock_render.kustomize_version.return_value = "v5.0.0"
        mock_render.cache_key.return_value = "cachekey"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result()
        mock_render.set_cached_render.return_value = None

        mock_diff.diff_rendered.return_value = _make_overlay_result(has_changes=True)

        base_wt = mock.MagicMock()
        base_wt.__enter__ = mock.MagicMock(return_value=base_wt)
        base_wt.__exit__ = mock.MagicMock(return_value=False)
        base_wt.path = Path("/tmp/base-wt")

        target_wt = mock.MagicMock()
        target_wt.__enter__ = mock.MagicMock(return_value=target_wt)
        target_wt.__exit__ = mock.MagicMock(return_value=False)
        target_wt.path = Path("/tmp/target-wt")

        mock_git.Worktree.side_effect = [base_wt, target_wt]

        result = pipeline.run_diff(Path("/repo"), ref="main~5", target_ref="main~2")

        assert result.ref == "short-main~5"
        assert result.target_ref == "short-main~2"
        assert result.has_changes
        assert len(result.overlays) == 1
        assert mock_git.Worktree.call_count == 2

    @mock.patch("kdrift.pipeline.diff")
    @mock.patch("kdrift.pipeline.render")
    @mock.patch("kdrift.pipeline.git")
    @mock.patch("kdrift.pipeline.discover")
    def test_baseline_build_failure(self, mock_discover, mock_git, mock_render, mock_diff):
        mock_git.resolve_ref.side_effect = lambda r, _: f"sha-{r}"
        mock_git.get_short_sha.side_effect = lambda r, _: f"short-{r}"
        mock_git.changed_files_between.return_value = [Path("k8s/dev/patch.yaml")]

        overlay = _make_overlay()
        graph = mock.MagicMock()
        graph.affected_overlays.return_value = [overlay]
        mock_discover.DependencyGraph.return_value = graph

        mock_render.DEFAULT_KUSTOMIZE_ARGS = ["--enable-helm"]
        mock_render.find_kustomize.return_value = "/usr/bin/kustomize"
        mock_render.kustomize_version.return_value = "v5.0.0"
        mock_render.cache_key.return_value = "cachekey"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result(success=False)

        base_wt = mock.MagicMock()
        base_wt.__enter__ = mock.MagicMock(return_value=base_wt)
        base_wt.__exit__ = mock.MagicMock(return_value=False)
        base_wt.path = Path("/tmp/base-wt")

        target_wt = mock.MagicMock()
        target_wt.__enter__ = mock.MagicMock(return_value=target_wt)
        target_wt.__exit__ = mock.MagicMock(return_value=False)
        target_wt.path = Path("/tmp/target-wt")

        mock_git.Worktree.side_effect = [base_wt, target_wt]

        result = pipeline.run_diff(Path("/repo"), ref="main~5", target_ref="main~2")

        assert result.has_errors
        assert "baseline build failed" in result.overlays[0].error


@pytest.mark.unit
class TestRenderWithCache:
    """Test the _render_with_cache helper."""

    @mock.patch("kdrift.pipeline.render")
    def test_returns_cached(self, mock_render):
        mock_render.cache_key.return_value = "key"
        mock_render.get_cached_render.return_value = "cached yaml"

        overlay = _make_overlay()
        ctx = _make_render_ctx()
        result = pipeline._render_with_cache(overlay, Path("/wt"), ctx, "abc123")

        assert result == "cached yaml"
        mock_render.render_overlay.assert_not_called()

    @mock.patch("kdrift.pipeline.render")
    def test_renders_and_caches(self, mock_render):
        mock_render.cache_key.return_value = "key"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result(output="fresh yaml")

        overlay = _make_overlay()
        ctx = _make_render_ctx()
        result = pipeline._render_with_cache(overlay, Path("/wt"), ctx, "abc123")

        assert result == "fresh yaml"
        mock_render.set_cached_render.assert_called_once_with("key", "fresh yaml")

    @mock.patch("kdrift.pipeline.render")
    def test_returns_none_on_failure(self, mock_render):
        mock_render.cache_key.return_value = "key"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result(success=False)

        overlay = _make_overlay()
        ctx = _make_render_ctx()
        result = pipeline._render_with_cache(overlay, Path("/wt"), ctx, "abc123")

        assert result is None
