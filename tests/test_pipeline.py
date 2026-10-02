"""Tests for the diff pipeline orchestration."""

from pathlib import Path
from unittest import mock

import pytest
import yaml

from kdrift import discover, models, pipeline


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

        graph = mock.MagicMock()
        graph.external_sources.return_value = []
        mock_discover.DependencyGraph.return_value = graph

        result = pipeline.run_diff(Path("/repo"))

        assert result.ref == "a1b2c3d"
        assert result.target_ref is None
        assert not result.has_changes
        assert result.warnings == []

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


@pytest.fixture()
def multi_overlay_repo(tmp_path):
    """A shared base consumed by three leaf overlays; dev also has its own patch."""
    base = tmp_path / "k8s" / "base"
    base.mkdir(parents=True)
    (base / "kustomization.yaml").write_text("resources:\n  - authconfig.yaml\n")
    (base / "authconfig.yaml").write_text("kind: AuthConfig\n")

    for env in ("staging", "prod"):
        d = tmp_path / "k8s" / env
        d.mkdir()
        (d / "kustomization.yaml").write_text("resources:\n  - ../base\n")

    dev = tmp_path / "k8s" / "dev"
    dev.mkdir()
    (dev / "kustomization.yaml").write_text("resources:\n  - ../base\npatches:\n  - path: patch.yaml\n")
    (dev / "patch.yaml").write_text("kind: AuthConfig\n")

    return tmp_path


@pytest.mark.unit
class TestSelectByPaths:
    """Path-based overlay selection against the affected set (regression: base-driven drift)."""

    def _graph(self, repo: Path) -> discover.DependencyGraph:
        graph = discover.DependencyGraph(repo)
        graph.build()
        return graph

    def test_base_change_scoped_to_single_overlay(self, multi_overlay_repo):
        # Regression: a base/ change affects staging transitively. Scoping to
        # [k8s/staging] must still return staging, not drop it as "no drift".
        graph = self._graph(multi_overlay_repo)
        affected = graph.affected_overlays([Path("k8s/base/authconfig.yaml")])
        selected, warnings = pipeline._select_by_paths(affected, [Path("k8s/staging")], graph, multi_overlay_repo)
        assert {str(o.path) for o in selected} == {"k8s/staging"}
        assert warnings == []

    def test_base_change_scoped_to_multiple_overlays(self, multi_overlay_repo):
        # Regression: paths=[dev,staging,prod] must return all three, not only
        # the one (dev) that had a direct file change.
        graph = self._graph(multi_overlay_repo)
        affected = graph.affected_overlays([Path("k8s/base/authconfig.yaml")])
        paths = [Path("k8s/dev"), Path("k8s/staging"), Path("k8s/prod")]
        selected, warnings = pipeline._select_by_paths(affected, paths, graph, multi_overlay_repo)
        assert {str(o.path) for o in selected} == {"k8s/dev", "k8s/staging", "k8s/prod"}
        assert warnings == []

    def test_upstream_base_path_selects_all_consumers(self, multi_overlay_repo):
        graph = self._graph(multi_overlay_repo)
        affected = graph.affected_overlays([Path("k8s/base/authconfig.yaml")])
        selected, _ = pipeline._select_by_paths(affected, [Path("k8s/base")], graph, multi_overlay_repo)
        assert {str(o.path) for o in selected} == {"k8s/dev", "k8s/staging", "k8s/prod"}

    def test_clean_overlay_path_warns_and_selects_nothing(self, multi_overlay_repo):
        # Only dev's own patch changed -> dev is the sole affected overlay.
        # Scoping to the clean staging overlay must warn, never over-match dev.
        graph = self._graph(multi_overlay_repo)
        affected = graph.affected_overlays([Path("k8s/dev/patch.yaml")])
        selected, warnings = pipeline._select_by_paths(affected, [Path("k8s/staging")], graph, multi_overlay_repo)
        assert selected == []
        assert any("no overlay with drift" in w for w in warnings)

    def test_file_within_overlay_selects_that_overlay(self, multi_overlay_repo):
        graph = self._graph(multi_overlay_repo)
        affected = graph.affected_overlays([Path("k8s/base/authconfig.yaml")])
        selected, _ = pipeline._select_by_paths(affected, [Path("k8s/dev/patch.yaml")], graph, multi_overlay_repo)
        assert {str(o.path) for o in selected} == {"k8s/dev"}

    def test_nonexistent_path_warning(self, multi_overlay_repo):
        warnings = pipeline._nonexistent_path_warnings([Path("k8s/nope")], multi_overlay_repo)
        assert any("does not exist" in w for w in warnings)


@pytest.mark.unit
class TestRunDiffPathsScoping:
    """run_diff must not pass `paths` to git as a pathspec (the original bug)."""

    @mock.patch("kdrift.pipeline.diff")
    @mock.patch("kdrift.pipeline.render")
    @mock.patch("kdrift.pipeline.git")
    @mock.patch("kdrift.pipeline.discover")
    def test_paths_not_forwarded_as_git_pathspec(self, mock_discover, mock_git, mock_render, mock_diff):
        mock_git.resolve_ref.return_value = "a" * 40
        mock_git.get_short_sha.return_value = "a1b2c3d"
        mock_git.changed_files.return_value = [Path("k8s/base/authconfig.yaml")]

        staging = _make_overlay("k8s/staging")
        graph = mock.MagicMock()
        graph.affected_overlays.return_value = [staging]
        graph.leaf_overlays = [staging]
        mock_discover.DependencyGraph.return_value = graph

        mock_render.DEFAULT_KUSTOMIZE_ARGS = ["--enable-helm"]
        mock_render.find_kustomize.return_value = "/usr/bin/kustomize"
        mock_render.kustomize_version.return_value = "v5.0.0"
        mock_render.render_overlays_parallel.return_value = [_make_render_result("k8s/staging")]
        mock_render.cache_key.return_value = "cachekey"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result("k8s/staging", output="baseline yaml")
        mock_diff.diff_rendered.return_value = _make_overlay_result("k8s/staging", has_changes=True)

        wt_mock = mock.MagicMock()
        wt_mock.__enter__ = mock.MagicMock(return_value=wt_mock)
        wt_mock.__exit__ = mock.MagicMock(return_value=False)
        wt_mock.path = Path("/tmp/wt")
        mock_git.Worktree.return_value = wt_mock

        result = pipeline.run_diff(Path("/repo"), paths=[Path("k8s/staging")])

        # The git helper must be called with paths=None: a pathspec would have
        # dropped the base/ change and hidden staging's drift.
        _, kwargs = mock_git.changed_files.call_args
        args = mock_git.changed_files.call_args.args
        assert kwargs.get("paths", args[1] if len(args) > 1 else None) is None
        assert result.has_changes
        assert {str(o.path) for o in result.overlays} == {"k8s/staging"}


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

        assert result.success
        assert result.output == "cached yaml"
        mock_render.render_overlay.assert_not_called()

    @mock.patch("kdrift.pipeline.render")
    def test_renders_and_caches(self, mock_render):
        mock_render.cache_key.return_value = "key"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result(output="fresh yaml")

        overlay = _make_overlay()
        ctx = _make_render_ctx()
        result = pipeline._render_with_cache(overlay, Path("/wt"), ctx, "abc123")

        assert result.success
        assert result.output == "fresh yaml"
        mock_render.set_cached_render.assert_called_once_with("key", "fresh yaml")

    @mock.patch("kdrift.pipeline.render")
    def test_returns_failure_result(self, mock_render):
        mock_render.cache_key.return_value = "key"
        mock_render.get_cached_render.return_value = None
        mock_render.render_overlay.return_value = _make_render_result(success=False)

        overlay = _make_overlay()
        ctx = _make_render_ctx()
        result = pipeline._render_with_cache(overlay, Path("/wt"), ctx, "abc123")

        assert not result.success
        mock_render.set_cached_render.assert_not_called()


@pytest.mark.unit
class TestPatchedChartHomes:
    """Test the live chartHome temp-patch context manager."""

    def test_noop_when_empty(self, tmp_path):
        kust = tmp_path / "app" / "kustomization.yaml"
        kust.parent.mkdir()
        original = "helmCharts:\n  - name: mychart\n"
        kust.write_text(original)

        with pipeline._patched_chart_homes(tmp_path, None):
            assert kust.read_text() == original
        assert kust.read_text() == original

    def test_patches_then_restores_exact_bytes(self, tmp_path):
        kust = tmp_path / "app" / "kustomization.yaml"
        kust.parent.mkdir()
        original = "helmCharts:\n  - name: mychart\n"  # no trailing reformat
        kust.write_bytes(original.encode())

        with pipeline._patched_chart_homes(tmp_path, {Path("app"): "/ext/charts"}):
            patched = kust.read_text()
            assert "chartHome: /ext/charts" in patched
            assert "mychart" in patched

        assert kust.read_bytes() == original.encode()

    def test_restores_on_exception(self, tmp_path):
        kust = tmp_path / "app" / "kustomization.yaml"
        kust.parent.mkdir()
        original = "helmGlobals:\n  chartHome: charts\nhelmCharts:\n  - name: mychart\n"
        kust.write_bytes(original.encode())

        with pytest.raises(RuntimeError), pipeline._patched_chart_homes(tmp_path, {Path("app"): "/ext/charts"}):
            assert "chartHome: /ext/charts" in kust.read_text()
            raise RuntimeError("boom")

        assert kust.read_bytes() == original.encode()

    def test_missing_kustomization_is_skipped(self, tmp_path):
        # An override naming a dir with no kustomization.yaml is a no-op, not a crash.
        with pipeline._patched_chart_homes(tmp_path, {Path("nope"): "/ext/charts"}):
            pass

    def test_strips_repo_from_helm_charts(self, tmp_path):
        # #29: a pinned repo: makes kustomize ignore chartHome and render the remote
        # chart, so the override must drop repo: on the candidate (restored after).
        kust = tmp_path / "app" / "kustomization.yaml"
        kust.parent.mkdir()
        original = "helmCharts:\n  - name: mychart\n    repo: oci://example.com\n    version: 1.0.0\n"
        kust.write_bytes(original.encode())

        with pipeline._patched_chart_homes(tmp_path, {Path("app"): "/ext/charts"}):
            patched = yaml.safe_load(kust.read_text())
            assert "repo" not in patched["helmCharts"][0]
            assert patched["helmCharts"][0]["version"] == "1.0.0"  # version left as-is
            assert patched["helmGlobals"]["chartHome"] == "/ext/charts"

        assert kust.read_bytes() == original.encode()


@pytest.mark.unit
class TestBaselineFailureError:
    """Test the baseline-failure message composition."""

    def _plan(self, **kwargs) -> pipeline._ExternalPlan:
        return pipeline._ExternalPlan(pinnable={}, unpinnable={}, repos={}, **kwargs)

    def test_pre_existing_includes_stderr(self):
        overlay = _make_overlay()
        result = models.RenderResult(overlay_path=overlay.path, error="Error: accumulating resources", exit_code=1)

        msg = pipeline._baseline_failure_error(result, overlay, self._plan(), {})

        assert msg == "baseline build failed (pre-existing): Error: accumulating resources"

    def test_pre_existing_without_stderr(self):
        overlay = _make_overlay()
        result = models.RenderResult(overlay_path=overlay.path, error="", exit_code=1)

        msg = pipeline._baseline_failure_error(result, overlay, self._plan(), {})

        assert msg == "baseline build failed (pre-existing)"

    def test_unresolved_dep_names_chart_and_stderr(self):
        overlay = _make_overlay()
        ref = models.ExternalChartRef(
            declaring_kust=Path("k8s/base"),
            chart_home_abs=Path("/other/charts"),
            chart_names=("app",),
        )
        plan = self._plan(deps_unresolved={Path("k8s/base"): ("app",)})
        result = models.RenderResult(
            overlay_path=overlay.path,
            error="missing in charts/ directory: postgresql",
            exit_code=1,
        )

        msg = pipeline._baseline_failure_error(result, overlay, plan, {overlay.path: [ref]})

        assert "helm deps unresolved in baseline worktree for chart(s) app" in msg
        assert "missing in charts/ directory: postgresql" in msg
