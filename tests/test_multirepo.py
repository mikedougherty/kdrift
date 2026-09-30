"""Tests for multi-repo diff: out-of-repo helm chart sources.

Split into git-only tests (external plan resolution, chartHome rewrite, cache
keying, warnings) that run wherever git is available, and a full end-to-end diff
test guarded by a kustomize+helm availability skip.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from kdrift import discover, models, pipeline, render

_HAVE_E2E = all(shutil.which(b) for b in ("kustomize", "helm", "git"))
requires_e2e = pytest.mark.skipif(not _HAVE_E2E, reason="needs kustomize + helm + git")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")


def _commit_all(repo: Path, msg: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


def _head(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _write_chart(chart_dir: Path, image: str = "nginx:1.0.0") -> None:
    (chart_dir / "templates").mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text("apiVersion: v2\nname: foo\nversion: 0.1.0\n")
    (chart_dir / "values.yaml").write_text(f"image: {image}\n")
    (chart_dir / "templates" / "deploy.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: foo\n"
        "spec:\n  template:\n    spec:\n      containers:\n"
        '        - name: foo\n          image: "{{ .Values.image }}"\n'
    )


@pytest.mark.unit
class TestExternalCacheKeyAndWarnings:
    def test_external_cache_key_empty_when_no_refs(self):
        plan = pipeline._ExternalPlan(pinnable={}, unpinnable={}, repos={})
        assert pipeline._external_cache_key([], plan) == []

    def test_external_cache_key_dedups_and_sorts(self):
        dep = pipeline._ExternalDep(Path("base"), Path("/b"), "sha1", Path("charts"))
        plan = pipeline._ExternalPlan(pinnable={Path("base"): dep}, unpinnable={}, repos={Path("/b"): "sha1"})
        ref = models.ExternalChartRef(declaring_kust=Path("base"), chart_home_abs=Path("/b/charts"))
        assert pipeline._external_cache_key([ref, ref], plan) == ["/b@sha1"]

    def test_cache_key_identical_when_external_empty(self):
        # Regression guard for the "no invalidation for single-repo repos" claim.
        base = render.cache_key("ref", Path("k8s/dev"), ["--enable-helm"], "v5")
        with_empty = render.cache_key("ref", Path("k8s/dev"), ["--enable-helm"], "v5", external=[])
        assert base == with_empty

    def test_cache_key_changes_with_external(self):
        base = render.cache_key("ref", Path("k8s/dev"), ["--enable-helm"], "v5")
        with_ext = render.cache_key("ref", Path("k8s/dev"), ["--enable-helm"], "v5", external=["/b@sha1"])
        assert base != with_ext

    def test_unpinnable_warning(self):
        plan = pipeline._ExternalPlan(pinnable={}, unpinnable={Path("base"): "not a git repo"}, repos={})
        overlay = models.Overlay(path=Path("app"), kustomization_file=Path("app/kustomization.yaml"))
        ref = models.ExternalChartRef(declaring_kust=Path("base"), chart_home_abs=Path("/ext/charts"))
        msgs = pipeline._unpinnable_warnings([overlay], {Path("app"): [ref]}, plan)
        assert len(msgs) == 1
        assert "/ext/charts" in msgs[0]
        assert "not a git repo" in msgs[0]

    def test_no_change_warning_only_for_unpinnable(self):
        assert pipeline._no_change_external_warnings(pipeline._ExternalPlan({}, {}, {})) == []
        plan = pipeline._ExternalPlan({}, {Path("base"): "read-only"}, {})
        msgs = pipeline._no_change_external_warnings(plan)
        assert len(msgs) == 1
        assert "read-only" in msgs[0]

    def test_demote_unrewritten_marks_unpinnable(self):
        dep_a = pipeline._ExternalDep(Path("a"), Path("/b"), "sha", Path("charts"))
        dep_c = pipeline._ExternalDep(Path("c"), Path("/b"), "sha", Path("charts"))
        plan = pipeline._ExternalPlan(
            pinnable={Path("a"): dep_a, Path("c"): dep_c}, unpinnable={}, repos={Path("/b"): "sha"}
        )
        pipeline._demote_unrewritten(plan, {Path("a")})
        assert Path("a") in plan.pinnable
        assert Path("c") not in plan.pinnable
        assert Path("c") in plan.unpinnable


@pytest.mark.unit
class TestBuildExternalPlan:
    def test_external_git_chart_is_pinnable(self, tmp_path):
        b = tmp_path / "repoB"
        _init_repo(b)
        (b / "charts").mkdir()
        (b / "charts" / ".keep").write_text("")
        _commit_all(b)

        a = tmp_path / "repoA"
        app = a / "app"
        app.mkdir(parents=True)
        (app / "kustomization.yaml").write_text(
            f"helmGlobals:\n  chartHome: {b / 'charts'}\nhelmCharts:\n  - name: foo\n"
        )
        _init_repo(a)
        _commit_all(a)

        graph = discover.DependencyGraph(a)
        graph.build()
        plan = pipeline._build_external_plan(graph, a)

        assert Path("app") in plan.pinnable
        dep = plan.pinnable[Path("app")]
        assert dep.b_root == b.resolve()
        assert dep.rel == Path("charts")
        assert b.resolve() in plan.repos

    def test_non_git_external_is_unpinnable(self, tmp_path):
        ext = tmp_path / "ext"  # not a git repo
        (ext / "charts").mkdir(parents=True)

        a = tmp_path / "repoA"
        app = a / "app"
        app.mkdir(parents=True)
        (app / "kustomization.yaml").write_text(
            f"helmGlobals:\n  chartHome: {ext / 'charts'}\nhelmCharts:\n  - name: foo\n"
        )
        _init_repo(a)
        _commit_all(a)

        graph = discover.DependencyGraph(a)
        graph.build()
        plan = pipeline._build_external_plan(graph, a)

        assert Path("app") in plan.unpinnable
        assert "not in a git repository" in plan.unpinnable[Path("app")]

    def test_chart_home_back_into_repo_is_unpinnable(self, tmp_path):
        # Absolute chartHome that resolves back into repo A (via a sibling symlink)
        # must not spawn a second A worktree.
        a = tmp_path / "repoA"
        app = a / "app"
        app.mkdir(parents=True)
        _init_repo(a)
        alias = tmp_path / "aliasA"
        alias.symlink_to(a)
        (app / "kustomization.yaml").write_text(
            f"helmGlobals:\n  chartHome: {alias / 'charts'}\nhelmCharts:\n  - name: foo\n"
        )
        (a / "charts").mkdir()
        (a / "charts" / ".keep").write_text("")
        _commit_all(a)

        graph = discover.DependencyGraph(a)
        graph.build()
        plan = pipeline._build_external_plan(graph, a)
        # A symlink into A resolves in-repo upstream (discover .resolve()s it), so it
        # is never classified external and never reaches the pipeline. Either way the
        # invariant holds: no second worktree of A.
        assert Path("app") not in plan.pinnable
        assert a.resolve() not in plan.repos

    def test_chart_home_at_repo_root_rel_is_dot(self, tmp_path):
        b = tmp_path / "repoB"
        _init_repo(b)
        (b / "mychart").mkdir()  # chart at repo top: chartHome == repo root
        (b / "mychart" / ".keep").write_text("")
        _commit_all(b)

        a = tmp_path / "repoA"
        app = a / "app"
        app.mkdir(parents=True)
        (app / "kustomization.yaml").write_text(f"helmGlobals:\n  chartHome: {b}\nhelmCharts:\n  - name: mychart\n")
        _init_repo(a)
        _commit_all(a)

        graph = discover.DependencyGraph(a)
        graph.build()
        plan = pipeline._build_external_plan(graph, a)
        assert plan.pinnable[Path("app")].rel == Path(".")


@pytest.mark.unit
class TestRewriteChartHomes:
    def test_sets_chart_home_creating_field_when_absent(self, tmp_path):
        wt = tmp_path / "wt"
        base = wt / "base"
        base.mkdir(parents=True)
        # kustomization with NO helmGlobals (symlink/relative-escape case: nothing to replace)
        (base / "kustomization.yaml").write_text("helmCharts:\n  - name: foo\n")
        dep = pipeline._ExternalDep(Path("base"), Path("/b"), "sha", Path("charts"))
        plan = pipeline._ExternalPlan(pinnable={Path("base"): dep}, unpinnable={}, repos={Path("/b"): "sha"})

        pipeline._rewrite_chart_homes(wt, plan, {Path("/b"): Path("/tmp/bwt")})

        data = yaml.safe_load((base / "kustomization.yaml").read_text())
        assert data["helmGlobals"]["chartHome"] == "/tmp/bwt/charts"
        assert data["helmCharts"] == [{"name": "foo"}]


@requires_e2e
@pytest.mark.integration
class TestMultiRepoDiffE2E:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        b = tmp_path / "repoB"
        _init_repo(b)
        _write_chart(b / "charts" / "foo", image="nginx:1.0.0")
        _commit_all(b, "chart 1.0.0")

        a = tmp_path / "repoA"
        app = a / "app"
        app.mkdir(parents=True)
        (app / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
            f"helmGlobals:\n  chartHome: {b / 'charts'}\n"
            "helmCharts:\n  - name: foo\n    releaseName: foo\n"
        )
        _init_repo(a)
        _commit_all(a, "A init")
        return a, b

    def test_external_chart_edit_shows_drift(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        a, b = self._setup(tmp_path)

        # Clean: no drift.
        clean = pipeline.run_diff(a)
        assert not clean.has_changes

        # Edit B's chart values (uncommitted in B).
        (b / "charts" / "foo" / "values.yaml").write_text("image: nginx:2.0.0\n")

        result = pipeline.run_diff(a)
        assert result.has_changes
        assert any(o.path == Path("app") for o in result.overlays if o.has_changes)

    def test_overlay_filter_nonleaf_base_shows_external_drift(self, tmp_path, monkeypatch):
        # Regression guard for the forced-non-leaf silent-miss: --overlay base where
        # base (a non-leaf) declares the external chartHome.
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        b = tmp_path / "repoB"
        _init_repo(b)
        _write_chart(b / "charts" / "foo", image="nginx:1.0.0")
        _commit_all(b, "chart 1.0.0")

        a = tmp_path / "repoA"
        base = a / "base"
        dev = a / "dev"
        base.mkdir(parents=True)
        dev.mkdir(parents=True)
        (base / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
            f"helmGlobals:\n  chartHome: {b / 'charts'}\n"
            "helmCharts:\n  - name: foo\n    releaseName: foo\n"
        )
        (dev / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - ../base\n"
        )
        _init_repo(a)
        _commit_all(a, "A init")

        (b / "charts" / "foo" / "values.yaml").write_text("image: nginx:2.0.0\n")

        result = pipeline.run_diff(a, overlay_filter=Path("base"))
        assert result.has_changes

    def test_ref_vs_ref_with_external_chart(self, tmp_path, monkeypatch):
        # Exercises the ref-vs-ref external path: both sides pin B@HEAD, so the
        # external chart is consistent and only the committed A-level change shows.
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        a, _b = self._setup(tmp_path)
        ref1 = _head(a)

        # Committed A-level change: add a namePrefix to the overlay.
        kust = a / "app" / "kustomization.yaml"
        kust.write_text(kust.read_text() + "namePrefix: pre-\n")
        _commit_all(a, "add namePrefix")
        ref2 = _head(a)

        result = pipeline.run_diff(a, ref=ref1, target_ref=ref2)
        assert not result.has_errors
        assert result.has_changes  # the namePrefix renames the rendered Deployment
        # No warnings proves the external chart stayed pinnable (not silently demoted
        # to a live read) through the ref-vs-ref rewrite path.
        assert result.warnings == []

    def test_unpinnable_non_git_external_warns_and_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        ext = tmp_path / "ext"  # not a git repo
        _write_chart(ext / "foo", image="nginx:1.0.0")

        a = tmp_path / "repoA"
        app = a / "app"
        app.mkdir(parents=True)
        (app / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
            f"helmGlobals:\n  chartHome: {ext}\n"
            "helmCharts:\n  - name: foo\n    releaseName: foo\n"
        )
        _init_repo(a)
        _commit_all(a, "A init")

        # Edit the non-git external chart; with no in-repo change, expect a warning
        # (source could not be checked) rather than a crash or a false all-clear.
        (ext / "foo" / "values.yaml").write_text("image: nginx:2.0.0\n")
        result = pipeline.run_diff(a)
        assert any("could not be checked" in w for w in result.warnings)
