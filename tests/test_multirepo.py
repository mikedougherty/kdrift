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
        dep = pipeline._ExternalDep(Path("base"), Path("/b"), "sha1", Path("charts"), ())
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
        dep_a = pipeline._ExternalDep(Path("a"), Path("/b"), "sha", Path("charts"), ())
        dep_c = pipeline._ExternalDep(Path("c"), Path("/b"), "sha", Path("charts"), ())
        plan = pipeline._ExternalPlan(
            pinnable={Path("a"): dep_a, Path("c"): dep_c}, unpinnable={}, repos={Path("/b"): "sha"}
        )
        pipeline._demote_unrewritten(plan, {Path("a")})
        assert Path("a") in plan.pinnable
        assert Path("c") not in plan.pinnable
        assert Path("c") in plan.unpinnable


@pytest.mark.unit
class TestHelmDepResolution:
    def test_helm_binary_default_and_override(self):
        assert pipeline._helm_binary(["--enable-helm"]) == "helm"
        assert pipeline._helm_binary(["--helm-command", "/usr/bin/helm"]) == "/usr/bin/helm"
        assert pipeline._helm_binary(["--helm-command=/opt/helm"]) == "/opt/helm"

    def test_within_guard(self, tmp_path):
        root = tmp_path / "wt"
        (root / "a").mkdir(parents=True)
        assert pipeline._within(root / "a" / "charts", root.resolve())
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "a" / "link").symlink_to(outside)
        assert not pipeline._within(root / "a" / "link" / "charts", root.resolve())

    def test_within_rejects_dangling_escaping_symlink(self, tmp_path):
        # A broken (dangling) symlink escaping the worktree must be rejected, not
        # admitted by walking past it to a benign ancestor.
        wt = tmp_path / "wt" / "c"
        wt.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()  # parent exists; the target file does not -> dangling
        (wt / "Chart.lock").symlink_to(outside / "Chart.lock")
        assert not pipeline._within(wt / "Chart.lock", (tmp_path / "wt").resolve())
        # a genuinely-absent non-symlink path inside the worktree still passes
        assert pipeline._within(wt / "charts", (tmp_path / "wt").resolve())

    def test_resolve_skip_dangling_symlink_never_writes_outside(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        self._chart(wt / "c", deps=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (wt / "c" / "Chart.lock").symlink_to(outside / "Chart.lock")  # dangling, escapes
        live = tmp_path / "live" / "c"
        (live / "charts").mkdir(parents=True)
        (live / "charts" / "foo-1.0.0.tgz").write_text("x")
        (live / "Chart.lock").write_text("LIVE LOCK\n")
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: False)
        result = pipeline._resolve_one_chart(wt / "c", live, wt.resolve(), "helm", None)
        assert result == "skip"
        # the dangling symlink's escaping target must NOT have been written through
        assert not (outside / "Chart.lock").exists()

    def test_parse_deps(self, tmp_path):
        assert pipeline._parse_deps(tmp_path / "nope.yaml") is None
        (tmp_path / "Chart.yaml").write_text("name: c\n")
        assert pipeline._parse_deps(tmp_path / "Chart.yaml") == []
        (tmp_path / "Chart.lock").write_text("dependencies:\n  - name: foo\n    version: 1.2.3\n")
        assert pipeline._parse_deps(tmp_path / "Chart.lock") == [("foo", "1.2.3")]

    def test_deps_complete(self, tmp_path):
        (tmp_path / "charts").mkdir()
        req = [("foo", "1.2.3")]
        assert not pipeline._deps_complete(tmp_path, req)
        (tmp_path / "charts" / "foo-1.2.3.tgz").write_text("x")
        assert pipeline._deps_complete(tmp_path, req)

    def _chart(self, d: Path, deps: bool) -> None:
        d.mkdir(parents=True)
        body = "name: c\nversion: 0.1.0\n"
        if deps:
            body += "dependencies:\n  - name: foo\n    version: 1.0.0\n    repository: file://../foo\n"
        (d / "Chart.yaml").write_text(body)

    def test_resolve_skip_no_chart_yaml(self, tmp_path):
        wt = tmp_path / "wt"
        (wt / "c").mkdir(parents=True)
        assert pipeline._resolve_one_chart(wt / "c", tmp_path / "live" / "c", wt.resolve(), "helm", None) == "skip"

    def test_resolve_ok_no_deps(self, tmp_path):
        wt = tmp_path / "wt"
        self._chart(wt / "c", deps=False)
        assert pipeline._resolve_one_chart(wt / "c", tmp_path / "live" / "c", wt.resolve(), "helm", None) == "ok"

    def test_resolve_ok_already_complete(self, tmp_path):
        wt = tmp_path / "wt"
        self._chart(wt / "c", deps=True)
        (wt / "c" / "charts").mkdir()
        (wt / "c" / "charts" / "foo-1.0.0.tgz").write_text("x")
        assert pipeline._resolve_one_chart(wt / "c", tmp_path / "live" / "c", wt.resolve(), "helm", None) == "ok"

    def test_resolve_primary_build_success(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        self._chart(wt / "c", deps=True)
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: True)
        assert pipeline._resolve_one_chart(wt / "c", tmp_path / "live" / "c", wt.resolve(), "helm", None) == "ok"

    def test_resolve_fallback_copy(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        self._chart(wt / "c", deps=True)
        live = tmp_path / "live" / "c" / "charts"
        live.mkdir(parents=True)
        (live / "foo-1.0.0.tgz").write_text("x")
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: False)
        assert pipeline._resolve_one_chart(wt / "c", tmp_path / "live" / "c", wt.resolve(), "helm", None) == "fallback"
        assert (wt / "c" / "charts" / "foo-1.0.0.tgz").exists()

    def test_resolve_copy_failure_degrades_to_unresolved(self, tmp_path, monkeypatch):
        # A shutil failure during the fallback copy must degrade this chart to
        # "unresolved" (baseline fails), never crash the whole diff.
        wt = tmp_path / "wt"
        self._chart(wt / "c", deps=True)
        live = tmp_path / "live" / "c"
        (live / "charts").mkdir(parents=True)
        (live / "charts" / "foo-1.0.0.tgz").write_text("x")
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: False)

        def boom(*_a, **_k):
            raise OSError("copy failed")

        monkeypatch.setattr(pipeline.shutil, "copytree", boom)
        result = pipeline._resolve_one_chart(wt / "c", live, wt.resolve(), "helm", None)
        assert result == "unresolved"

    def test_resolve_unresolved(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        self._chart(wt / "c", deps=True)
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: False)
        # no live charts/ to copy
        result = pipeline._resolve_one_chart(wt / "c", tmp_path / "live" / "c", wt.resolve(), "helm", None)
        assert result == "unresolved"

    def test_resolve_skip_escaping_symlink_never_copies(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside"
        self._chart(outside, deps=True)
        (wt / "c").symlink_to(outside)  # chart dir escapes the worktree
        live = tmp_path / "live" / "c" / "charts"
        live.mkdir(parents=True)
        (live / "foo-1.0.0.tgz").write_text("x")
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: False)
        assert pipeline._resolve_one_chart(wt / "c", tmp_path / "live" / "c", wt.resolve(), "helm", None) == "skip"
        # the escaping target must NOT have been written through
        assert not (outside / "charts").exists()

    def test_resolve_external_deps_records_fallback(self, tmp_path, monkeypatch):
        wt = tmp_path / "bwt"
        self._chart(wt / "charts" / "mychart", deps=True)
        live = tmp_path / "live" / "charts" / "mychart" / "charts"
        live.mkdir(parents=True)
        (live / "foo-1.0.0.tgz").write_text("x")
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: False)
        b_root = tmp_path / "live"
        dep = pipeline._ExternalDep(Path("app"), b_root, "sha", Path("charts"), ("mychart",))
        plan = pipeline._ExternalPlan(pinnable={Path("app"): dep}, unpinnable={}, repos={b_root: "sha"})
        pipeline._resolve_external_deps(plan, {b_root: wt}, ["--enable-helm"], None)
        assert Path("app") in plan.deps_from_worktree

    def test_resolve_external_deps_records_unresolved(self, tmp_path, monkeypatch):
        wt = tmp_path / "bwt"
        self._chart(wt / "charts" / "mychart", deps=True)
        # no live deps to copy -> helm build fails and the copy fallback fails -> unresolved
        monkeypatch.setattr(pipeline, "_helm_dep_build", lambda *a, **k: False)
        b_root = tmp_path / "live"
        dep = pipeline._ExternalDep(Path("app"), b_root, "sha", Path("charts"), ("mychart",))
        plan = pipeline._ExternalPlan(pinnable={Path("app"): dep}, unpinnable={}, repos={b_root: "sha"})
        pipeline._resolve_external_deps(plan, {b_root: wt}, ["--enable-helm"], None)
        assert plan.deps_unresolved.get(Path("app")) == ("mychart",)
        assert Path("app") not in plan.deps_from_worktree

    def test_deps_from_worktree_is_noncacheable_and_warns(self):
        overlay = models.Overlay(path=Path("app"), kustomization_file=Path("app/kustomization.yaml"))
        ref = models.ExternalChartRef(declaring_kust=Path("base"), chart_home_abs=Path("/ext/charts"))
        plan = pipeline._ExternalPlan(
            pinnable={Path("base"): pipeline._ExternalDep(Path("base"), Path("/ext"), "sha", Path("charts"), ("c",))},
            unpinnable={},
            repos={Path("/ext"): "sha"},
            deps_from_worktree={Path("base")},
        )
        ext_by_leaf = {Path("app"): [ref]}
        _ext_key, cacheable = pipeline._overlay_external(overlay, ext_by_leaf, plan)
        assert cacheable is False
        wt_msgs = pipeline._worktree_deps_warnings([overlay], ext_by_leaf, plan)
        assert len(wt_msgs) == 1 and "working tree" in wt_msgs[0]
        # the un-pinnable warning must NOT fire for a worktree-resolved overlay
        assert pipeline._unpinnable_warnings([overlay], ext_by_leaf, plan) == []

    def test_cache_key_helm_ver_guarded(self):
        base = render.cache_key("r", Path("k8s/dev"), ["--enable-helm"], "v5", external=["/b@sha"])
        with_helm = render.cache_key("r", Path("k8s/dev"), ["--enable-helm"], "v5", external=["/b@sha"], helm_ver="v4")
        assert base != with_helm
        # helm_ver absent -> identical to the no-helm key (guarded)
        assert render.cache_key("r", Path("k8s/dev"), ["--enable-helm"], "v5") == render.cache_key(
            "r", Path("k8s/dev"), ["--enable-helm"], "v5", helm_ver=None
        )


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
        dep = pipeline._ExternalDep(Path("base"), Path("/b"), "sha", Path("charts"), ())
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


@requires_e2e
@pytest.mark.integration
class TestHelmDepE2E:
    def test_gitignored_dep_resolved_in_baseline(self, tmp_path, monkeypatch):
        # External repo B: a parent chart whose subchart dependency (file://) is
        # resolved into a gitignored charts/ — the common real-world convention.
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))  # kdrift render cache
        # Isolate helm to empty config/cache/data: zero configured repos, so a pure
        # file:// dependency resolves offline (no network, no dependence on the
        # developer's helm repos) for both the setup update and kdrift's build.
        helm_home = tmp_path / "helm"
        monkeypatch.setenv("HELM_CONFIG_HOME", str(helm_home / "config"))
        monkeypatch.setenv("HELM_CACHE_HOME", str(helm_home / "cache"))
        monkeypatch.setenv("HELM_DATA_HOME", str(helm_home / "data"))
        b = tmp_path / "repoB"
        charts = b / "charts"

        sub = charts / "subchart"
        (sub / "templates").mkdir(parents=True)
        (sub / "Chart.yaml").write_text("apiVersion: v2\nname: subchart\nversion: 0.1.0\n")
        (sub / "templates" / "sc.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sub-cm\ndata:\n  k: v\n"
        )

        sysd = charts / "system"
        (sysd / "templates").mkdir(parents=True)
        (sysd / "Chart.yaml").write_text(
            "apiVersion: v2\nname: system\nversion: 0.1.0\n"
            'dependencies:\n  - name: subchart\n    version: 0.1.0\n    repository: "file://../subchart"\n'
        )
        (sysd / "values.yaml").write_text("label: original\n")
        (sysd / "templates" / "cm.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sys-cm\ndata:\n  label: {{ .Values.label }}\n"
        )
        # Resolve deps -> Chart.lock + charts/system/charts/subchart-0.1.0.tgz (offline, file://).
        subprocess.run(["helm", "dependency", "update", str(sysd)], check=True, capture_output=True, text=True)
        # Gitignore the resolved tarballs so HEAD lacks them (the scenario #23 targets).
        (b / ".gitignore").write_text("charts/system/charts/\n")
        _init_repo(b)
        _commit_all(b, "chart + lock; resolved deps gitignored")

        # Consumer repo A points chartHome at B's local charts and inflates `system`.
        a = tmp_path / "repoA"
        app = a / "app"
        app.mkdir(parents=True)
        (app / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
            f"helmGlobals:\n  chartHome: {charts}\n"
            "helmCharts:\n  - name: system\n    releaseName: sys\n"
        )
        _init_repo(a)
        _commit_all(a, "A init")

        # Uncommitted edit in B's chart -> drift the baseline must resolve deps to show.
        (sysd / "values.yaml").write_text("label: changed\n")

        result = pipeline.run_diff(a)
        # Baseline resolved the gitignored dep via `helm dependency build` and rendered.
        assert not result.has_errors
        assert result.has_changes
        assert result.warnings == []  # ref-resolved (not the working-tree fallback)
