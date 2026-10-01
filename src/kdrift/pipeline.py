"""Shared render-diff orchestration for all frontends.

Single function that goes from changed files to structured diff results.
Called by CLI, watch mode, and future MCP/LSP servers.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os.path
import shutil
import subprocess
from pathlib import Path

import structlog
import yaml

from kdrift import diff, discover, git, models, render

log: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class _RenderContext:
    """Shared state for render operations within a single pipeline run."""

    repo_root: Path
    args: list[str]
    binary: str
    kust_ver: str
    env: dict[str, str] | None = None
    helm_ver: str | None = None  # set when external charts may be helm-resolved


@dataclasses.dataclass(frozen=True)
class _ExternalDep:
    """A pinnable out-of-repo chart dependency of one declaring kustomization."""

    declaring_kust: Path
    b_root: Path
    b_head: str
    rel: Path  # chart_home_abs relative to b_root
    chart_names: tuple[str, ...]  # referenced charts under this chartHome


@dataclasses.dataclass
class _ExternalPlan:
    """Per-run resolution of out-of-repo chart sources into pinnable / not.

    Three baseline states per declaring kustomization: pinnable + not in
    ``deps_from_worktree`` = ref-resolved (cacheable); pinnable + in
    ``deps_from_worktree`` = resolved from the working tree (diffed, NOT cached,
    distinct warning); ``unpinnable`` = not diffed at all. ``deps_from_worktree``
    is populated during the diff by the dependency-resolution step.
    """

    pinnable: dict[Path, _ExternalDep]  # declaring_kust -> dep
    unpinnable: dict[Path, str]  # declaring_kust -> reason
    repos: dict[Path, str]  # external repo root -> HEAD sha (dedup)
    deps_from_worktree: set[Path] = dataclasses.field(default_factory=set)  # declaring_kusts
    # declaring_kust -> chart names whose deps could not be resolved in the baseline
    # worktree (helm dependency build and the copy fallback both failed); the baseline
    # render will fail, and these name the chart(s) in that failure message.
    deps_unresolved: dict[Path, tuple[str, ...]] = dataclasses.field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.pinnable or self.unpinnable)


def _build_external_plan(graph: discover.DependencyGraph, repo_root: Path) -> _ExternalPlan:
    """Resolve each external chart ref to a git repo + baseline HEAD, or mark it un-pinnable.

    Un-pinnable = the chart source is not in a git repo, resolves back into the
    analyzed repo, is not under its repo root, or its HEAD cannot be resolved. Such
    a source cannot be redirected to a baseline worktree, so its drift is not
    captured (and its baseline must not be cached).
    """
    repo_real = repo_root.resolve()
    pinnable: dict[Path, _ExternalDep] = {}
    unpinnable: dict[Path, str] = {}
    repos: dict[Path, str] = {}

    for ref in graph.external_chart_refs():
        home = ref.chart_home_abs
        chart_names = ref.chart_names
        b_root = git.find_repo_root_or_none(home)
        if b_root is None:
            unpinnable.setdefault(ref.declaring_kust, "chart source is not in a git repository")
            continue
        b_root = b_root.resolve()
        if b_root == repo_real:
            unpinnable.setdefault(ref.declaring_kust, "chart source resolves back into this repository")
            continue
        try:
            rel = home.relative_to(b_root)
        except ValueError:
            unpinnable.setdefault(ref.declaring_kust, "chart home is not under its repo root")
            continue
        try:
            b_head = git.resolve_ref("HEAD", b_root)
        except git.GitError:
            unpinnable.setdefault(ref.declaring_kust, "chart repo HEAD could not be resolved")
            continue
        # `git worktree add` writes into <repo>/.git/worktrees; a read-only .git
        # (e.g. a CI-cached checkout owned by root) cannot be pinned. Only trust
        # this for a normal .git directory; a gitfile (submodule/linked worktree)
        # points elsewhere, so let the worktree attempt + demotion net catch it.
        gitdir = b_root / ".git"
        if gitdir.is_dir() and not os.access(gitdir, os.W_OK):
            unpinnable.setdefault(ref.declaring_kust, "chart repo is read-only (cannot create a worktree)")
            continue
        repos[b_root] = b_head
        pinnable[ref.declaring_kust] = _ExternalDep(ref.declaring_kust, b_root, b_head, rel, chart_names)

    return _ExternalPlan(pinnable=pinnable, unpinnable=unpinnable, repos=repos)


def _external_changed_files(plan: _ExternalPlan) -> list[Path]:
    """Absolute changed-file paths from every pinnable external repo (HEAD vs working)."""
    changed: list[Path] = []
    for b_root in plan.repos:
        try:
            for rel in git.changed_files("HEAD", repo_root=b_root):
                changed.append(b_root / rel)
        except git.GitError:
            log.warning("external_repo_diff_failed", repo=str(b_root))
    return changed


def _external_cache_key(refs: list[models.ExternalChartRef], plan: _ExternalPlan) -> list[str]:
    """`<b_root>@<sha>` entries for an overlay's pinnable external repos (for cache_key)."""
    entries = set()
    for ref in refs:
        dep = plan.pinnable.get(ref.declaring_kust)
        if dep is not None:
            entries.add(f"{dep.b_root}@{dep.b_head}")
    return sorted(entries)


# `helm dependency build` can hit the network; bound it so --watch/LSP never hang.
HELM_DEP_BUILD_TIMEOUT = 120  # seconds


def _helm_binary(kustomize_args: list[str]) -> str:
    """The helm binary kustomize uses (--helm-command), defaulting to 'helm'."""
    for i, arg in enumerate(kustomize_args):
        if arg == "--helm-command" and i + 1 < len(kustomize_args):
            return kustomize_args[i + 1]
        if arg.startswith("--helm-command="):
            return arg.split("=", 1)[1]
    return "helm"


def _within(path: Path, outer_real: Path) -> bool:
    """True if ``path`` resolves to a location inside ``outer_real``.

    Confinement guard: a chart dir, or a write target inside it, that resolves
    outside the worktree is an escaping symlink — writing there would mutate the
    live checkout, so the caller must skip it. ``Path.resolve()`` (non-strict)
    follows every symlink and still resolves a genuinely-absent non-symlink path to
    its in-worktree location, so it rejects an escaping symlink (including a
    *dangling* one) while admitting a not-yet-created dir. (Do NOT walk up to the
    nearest existing ancestor — that ascends past a broken escaping symlink and
    wrongly admits it, which lets the fallback copy write outside the worktree.)
    """
    try:
        path.resolve().relative_to(outer_real)
    except (ValueError, OSError):
        return False
    return True


def _parse_deps(f: Path) -> list[tuple[str, str]] | None:
    """Parse a chart's dependency (name, version) pairs from Chart.yaml/Chart.lock.

    Returns None if the file is absent/unreadable, [] if it has no dependencies.
    """
    if not f.is_file():
        return None
    try:
        data = yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    deps = data.get("dependencies")
    if not isinstance(deps, list):
        return []
    out: list[tuple[str, str]] = []
    for d in deps:
        if isinstance(d, dict) and isinstance(d.get("name"), str) and d["name"]:
            out.append((d["name"], str(d.get("version", ""))))
    return out


def _deps_complete(chart_dir: Path, required: list[tuple[str, str]]) -> bool:
    """True if every required dependency has a matching tarball/dir under charts/.

    Matches the declared/pinned deps against present artifacts rather than testing
    that charts/ is merely non-empty, so a partially-vendored charts/ is treated as
    incomplete.
    """
    charts = chart_dir / "charts"
    for name, ver in required:
        if (charts / f"{name}-{ver}.tgz").exists() or (charts / name).is_dir():
            continue
        return False
    return True


def _helm_dep_build(chart_dir: Path, binary: str, env: dict[str, str] | None, timeout: int) -> bool:
    """Run `helm dependency build` in ``chart_dir``; True on success."""
    subprocess_env = {**os.environ, **env} if env else None
    try:
        result = subprocess.run(
            [binary, "dependency", "build", str(chart_dir)],
            capture_output=True,
            text=True,
            check=False,
            env=subprocess_env,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        log.debug("helm_dependency_build_failed", chart=str(chart_dir), stderr=result.stderr.strip())
    return result.returncode == 0


def _copy_live_deps(live_chart_dir: Path, chart_dir: Path) -> bool:
    """Copy resolved charts/ (+ Chart.lock) from the live checkout into the worktree chart.

    Cleans any partial charts//tmpcharts/ a failed build left behind first. Only
    called for a chart dir already confirmed inside the worktree (never through an
    escaping symlink). Returns True if live deps were present and copied.
    """
    live_charts = live_chart_dir / "charts"
    if not live_charts.is_dir():
        return False
    try:
        shutil.rmtree(chart_dir / "charts", ignore_errors=True)
        shutil.rmtree(chart_dir / "tmpcharts", ignore_errors=True)
        shutil.copytree(live_charts, chart_dir / "charts", dirs_exist_ok=True)
        live_lock = live_chart_dir / "Chart.lock"
        if live_lock.is_file():
            shutil.copy2(live_lock, chart_dir / "Chart.lock")
    except OSError as e:
        # One unreadable file / a surviving escaping symlink must degrade this chart
        # to "unresolved", not crash the whole diff.
        log.warning("copy_live_deps_failed", chart=str(chart_dir), error=str(e))
        return False
    return True


def _resolve_one_chart(  # noqa: PLR0911
    chart_dir: Path,
    live_chart_dir: Path,
    worktree_real: Path,
    binary: str,
    env: dict[str, str] | None,
) -> str:
    """Ensure one chart's deps are present in the baseline worktree.

    Returns: 'ok' (ref-resolved or nothing to do), 'fallback' (deps copied from
    the working tree — baseline must not be cached), 'skip' (escaping symlink or
    no chart at the ref — never written through), or 'unresolved' (deps needed but
    neither build nor copy worked — baseline will fail).
    """
    # Confinement: the chart dir and its write targets must resolve inside the worktree.
    if not _within(chart_dir, worktree_real):
        return "skip"
    if not _within(chart_dir / "charts", worktree_real) or not _within(chart_dir / "Chart.lock", worktree_real):
        return "skip"
    if not (chart_dir / "Chart.yaml").is_file():
        return "skip"

    yaml_deps = _parse_deps(chart_dir / "Chart.yaml")
    if not yaml_deps:
        return "ok"  # no declared dependencies
    lock_deps = _parse_deps(chart_dir / "Chart.lock")
    required = lock_deps or yaml_deps  # prefer Chart.lock's pinned versions; empty/absent -> Chart.yaml
    if _deps_complete(chart_dir, required):
        return "ok"
    if _helm_dep_build(chart_dir, binary, env, HELM_DEP_BUILD_TIMEOUT):
        return "ok"
    if _copy_live_deps(live_chart_dir, chart_dir):
        return "fallback"
    return "unresolved"


def _resolve_external_deps(
    plan: _ExternalPlan,
    b_worktrees: dict[Path, Path],
    kustomize_args: list[str],
    env: dict[str, str] | None,
) -> None:
    """Resolve helm deps for each pinnable external chart in its B worktree.

    Runs once per B worktree (shared across ref-vs-ref's two A-rewrites). Records
    declaring kustomizations whose deps were copied from the working tree in
    ``plan.deps_from_worktree`` — their baseline is diffed but not cached and gets
    a distinct warning. Must run after rewrite/demotion and before the render loop.
    """
    binary = _helm_binary(kustomize_args)
    for declaring_kust, dep in plan.pinnable.items():
        wt = b_worktrees.get(dep.b_root)
        if wt is None:
            continue
        worktree_real = wt.resolve()
        for name in dep.chart_names:
            status = _resolve_one_chart(
                wt / dep.rel / name,
                dep.b_root / dep.rel / name,
                worktree_real,
                binary,
                env,
            )
            if status == "fallback":
                plan.deps_from_worktree.add(declaring_kust)
            elif status == "unresolved":
                plan.deps_unresolved[declaring_kust] = (*plan.deps_unresolved.get(declaring_kust, ()), name)


def _rewrite_chart_homes(
    worktree_root: Path,
    plan: _ExternalPlan,
    b_worktrees: dict[Path, Path],
) -> set[Path]:
    """Set helmGlobals.chartHome in an A worktree to point at the B baseline worktrees.

    Unconditional set (not a text replace): works whether the original chartHome
    was absolute, a symlink, a ``../`` escape, or absent. One declaring
    kustomization has one helmGlobals, so this rewrites each at most once. Returns
    the set of declaring kustomizations that were actually rewritten; a caller
    must treat any pinnable ref NOT in this set as un-pinnable (its baseline was
    not redirected, so it read the external source live and must not be cached).
    """
    rewritten: set[Path] = set()
    for declaring_kust, dep in plan.pinnable.items():
        kust_file = _find_kustomization_in(worktree_root / declaring_kust)
        if kust_file is None:
            continue
        try:
            data = yaml.safe_load(kust_file.read_text()) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        globals_field = data.get("helmGlobals")
        if not isinstance(globals_field, dict):
            globals_field = {}
            data["helmGlobals"] = globals_field
        globals_field["chartHome"] = str(b_worktrees[dep.b_root] / dep.rel)
        kust_file.write_text(yaml.safe_dump(data, sort_keys=False))
        rewritten.add(declaring_kust)
    return rewritten


def _demote_unrewritten(plan: _ExternalPlan, rewritten: set[Path]) -> None:
    """Mark pinnable refs whose baseline chartHome was not rewritten as un-pinnable.

    An un-rewritten baseline read the external source live, so it must not be
    cached under an external-keyed entry (that would be a stale/fabricated hit).

    Deliberately does NOT pop from ``plan.repos``: a repo may back several
    declaring kustomizations, and only this one failed to rewrite. ``plan.repos``
    is not read again after worktree setup anyway (cache keys read ``pinnable``).
    """
    for kust in list(plan.pinnable):
        if kust not in rewritten:
            plan.pinnable.pop(kust)
            plan.unpinnable.setdefault(kust, "chart home could not be rewritten in the baseline worktree")


def _find_kustomization_in(directory: Path) -> Path | None:
    for name in discover.KUSTOMIZATION_FILENAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def run_diff(  # noqa: PLR0913
    repo_root: Path,
    ref: str = "HEAD",
    paths: list[Path] | None = None,
    overlay_filter: Path | None = None,
    kustomize_args: list[str] | None = None,
    target_ref: str | None = None,
    kustomize_env: dict[str, str] | None = None,
) -> models.DiffResult:
    """Run the full discover -> render -> diff pipeline.

    Args:
        repo_root: Git repository root.
        ref: Git ref for baseline comparison.
        paths: Select which affected overlays to report. Each path selects an
            overlay when it names the overlay directory (or an ancestor of it),
            names a file inside the overlay, or names an upstream input the
            overlay depends on (e.g. a shared ``base/``). Selection runs against
            the full affected set computed from *all* changes, so an overlay is
            reported even when its only change comes from a shared base outside
            the given paths. Paths that match no overlay, or that select an
            overlay with no drift, are surfaced as non-fatal warnings.
        overlay_filter: Force-diff exactly this one overlay, regardless of
            whether anything changed. Takes precedence over ``paths``.
        kustomize_args: Override kustomize build flags.
        target_ref: When set, compare ref vs target_ref (two committed states)
            instead of ref vs working tree.
        kustomize_env: Extra env vars to inject into kustomize subprocesses.

    Returns:
        DiffResult with per-overlay, per-resource changes.
    """
    args = kustomize_args if kustomize_args is not None else render.DEFAULT_KUSTOMIZE_ARGS

    resolved_ref = git.resolve_ref(ref, repo_root)
    short_ref = git.get_short_sha(ref, repo_root)

    resolved_target: str | None = None
    short_target: str | None = None
    if target_ref is not None:
        resolved_target = git.resolve_ref(target_ref, repo_root)
        short_target = git.get_short_sha(target_ref, repo_root)

    # NOTE: `paths` is deliberately NOT passed to the git helpers as a pathspec.
    # A pathspec would drop changes outside the given paths (e.g. a shared
    # base/), hiding drift in an overlay that is affected only transitively.
    # Instead we detect all changes, resolve the full affected set through the
    # dependency graph, then select overlays by path afterwards.
    if target_ref is not None:
        changed = git.changed_files_between(ref, target_ref, None, repo_root)
    else:
        changed = git.changed_files(ref, None, repo_root)

    warnings = _nonexistent_path_warnings(paths, repo_root) if paths else []

    graph = discover.DependencyGraph(repo_root)
    graph.build()

    # Out-of-repo chart sources: resolve to owning git repos, and fold their
    # working-tree changes into the change set so an edit in an external chart
    # marks the consuming overlay affected. ref-vs-ref pins both sides to the
    # external HEAD, so external working-tree changes don't apply there.
    plan = _build_external_plan(graph, repo_root)
    if target_ref is None and plan.repos:
        changed = [*changed, *_external_changed_files(plan)]

    if not changed:
        log.info("no_changes_detected", ref=short_ref, target_ref=short_target)
        warnings.extend(_no_change_external_warnings(plan))
        return models.DiffResult(ref=short_ref, target_ref=short_target, warnings=warnings)

    log.debug("changed_files", count=len(changed), ref=short_ref, target_ref=short_target)

    if overlay_filter is not None:
        kust = discover._find_kustomization_in(repo_root / overlay_filter)
        if kust is None:
            return models.DiffResult(
                ref=short_ref,
                target_ref=short_target,
                errors=[f"No kustomization.yaml found in {overlay_filter}"],
                warnings=warnings,
            )
        affected = [
            models.Overlay(
                path=overlay_filter,
                kustomization_file=kust.relative_to(repo_root),
            )
        ]
    else:
        affected = graph.affected_overlays(changed)
        if paths:
            affected, select_warnings = _select_by_paths(affected, paths, graph, repo_root)
            warnings.extend(select_warnings)

    if not affected:
        log.info("no_affected_overlays", ref=short_ref, target_ref=short_target)
        return models.DiffResult(ref=short_ref, target_ref=short_target, warnings=warnings)

    log.info("affected_overlays", count=len(affected), overlays=[str(o.path) for o in affected])

    ext_by_leaf = graph.external_refs_for(affected) if plan.active else {}

    ctx = _RenderContext(
        repo_root=repo_root,
        args=args,
        binary=render.find_kustomize(),
        kust_ver=render.kustomize_version(),
        env=kustomize_env,
        # helm resolves out-of-repo chart deps into the baseline, so a helm upgrade
        # must invalidate those cached baselines; only computed when external.
        helm_ver=render.helm_version(_helm_binary(args)) if plan.active else None,
    )

    overlay_results: list[models.OverlayResult] = []
    errors: list[str] = []

    if target_ref is not None:
        assert resolved_target is not None
        _diff_ref_vs_ref(affected, ctx, resolved_ref, resolved_target, overlay_results, plan, ext_by_leaf)
    else:
        _diff_working_tree_vs_ref(affected, ctx, resolved_ref, overlay_results, plan, ext_by_leaf)

    # Emit these warnings AFTER the diff: the render step mutates the plan at
    # runtime — demoting a pinnable source (worktree-add failure or un-rewritable
    # chartHome) into unpinnable, and recording charts whose deps were resolved
    # from the working tree — so both sets are final only here.
    warnings.extend(_unpinnable_warnings(affected, ext_by_leaf, plan))
    warnings.extend(_worktree_deps_warnings(affected, ext_by_leaf, plan))

    return models.DiffResult(
        ref=short_ref,
        target_ref=short_target,
        overlays=overlay_results,
        errors=errors,
        warnings=warnings,
    )


def _unpinnable_warnings(
    affected: list[models.Overlay],
    ext_by_leaf: dict[Path, list[models.ExternalChartRef]],
    plan: _ExternalPlan,
) -> list[str]:
    """Warn per overlay whose external chart source cannot be pinned (thus not diffed)."""
    msgs: list[str] = []
    for overlay in affected:
        reasons = [
            f"{ref.chart_home_abs} ({plan.unpinnable[ref.declaring_kust]})"
            for ref in ext_by_leaf.get(overlay.path, [])
            if ref.declaring_kust in plan.unpinnable
        ]
        if reasons:
            msgs.append(f"overlay '{overlay.path}': out-of-repo chart drift not captured — {'; '.join(reasons)}")
    return msgs


def _worktree_deps_warnings(
    affected: list[models.Overlay],
    ext_by_leaf: dict[Path, list[models.ExternalChartRef]],
    plan: _ExternalPlan,
) -> list[str]:
    """Warn per overlay whose baseline helm deps were resolved from the working tree.

    Distinct from the un-pinnable warning: the overlay WAS diffed (deps copied from
    the live checkout), but against working-tree deps rather than the baseline ref,
    so dependency-version drift is not captured and the baseline is not cached.
    """
    msgs: list[str] = []
    for overlay in affected:
        sources = [
            str(ref.chart_home_abs)
            for ref in ext_by_leaf.get(overlay.path, [])
            if ref.declaring_kust in plan.deps_from_worktree
        ]
        if sources:
            msgs.append(
                f"overlay '{overlay.path}': baseline helm dependencies resolved from the working tree, "
                f"not the baseline ref (registry unreachable?); dependency-version drift is not "
                f"captured — {', '.join(sources)}"
            )
    return msgs


def _no_change_external_warnings(plan: _ExternalPlan) -> list[str]:
    """Warn when a no-change diff still can't vouch for un-pinnable external charts.

    Pinnable external repos are folded into the change set (their edits show as
    changes), so a genuine no-change result means they are clean. Un-pinnable
    sources (non-git, read-only, or resolving back into the repo) cannot be
    checked at all, so a clean result would be misleading for them.
    """
    if not plan.unpinnable:
        return []
    reasons = "; ".join(f"{kust} ({reason})" for kust, reason in sorted(plan.unpinnable.items()))
    return [
        f"no changes detected, but out-of-repo chart source(s) could not be checked "
        f"(drift there is invisible): {reasons}"
    ]


def _normalize_path(path: Path, repo_root: Path) -> Path:
    """Normalize a user-supplied path to a repo-root-relative form."""
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo_root.resolve())
        except ValueError:
            return path
    return Path(os.path.normpath(str(path)))


def _is_within(inner: Path, outer: Path) -> bool:
    """True if `inner` equals `outer` or is nested under it."""
    if inner == outer:
        return True
    try:
        inner.relative_to(outer)
    except ValueError:
        return False
    return True


def _path_selects_overlay(overlay_dir: Path, path: Path) -> bool:
    """True if `path` names the overlay, an ancestor of it, or a file within it."""
    return _is_within(overlay_dir, path) or _is_within(path, overlay_dir)


def _nonexistent_path_warnings(paths: list[Path], repo_root: Path) -> list[str]:
    """Warn for selection paths that don't exist on disk (likely typos)."""
    warnings: list[str] = []
    for raw in paths:
        norm = _normalize_path(raw, repo_root)
        if not (repo_root / norm).exists():
            warnings.append(f"path '{norm}' does not exist in the repository — no overlays selected for it")
    return warnings


def _select_by_paths(
    affected: list[models.Overlay],
    paths: list[Path],
    graph: discover.DependencyGraph,
    repo_root: Path,
) -> tuple[list[models.Overlay], list[str]]:
    """Filter the affected overlays down to those the given paths select.

    A path selects an affected overlay when it (a) names the overlay directory
    or an ancestor of it, (b) names a file inside the overlay, or (c) names an
    upstream input the overlay depends on (a shared base/component). Selection
    runs against the already-computed affected set, so transitive drift via a
    shared base is preserved. Existing paths that select no drifting overlay
    yield a non-fatal warning so an empty result isn't misread as "no drift".
    """
    selected: dict[Path, models.Overlay] = {}
    warnings: list[str] = []
    all_leaves = [o.path for o in graph.leaf_overlays]

    for raw in paths:
        norm = _normalize_path(raw, repo_root)

        # A path is a "target" when it names a known leaf overlay, an ancestor
        # of one, or a file inside one. Otherwise it's treated as an upstream
        # input (a shared base/component). The distinction matters: an overlay
        # directory that is simply clean must warn, not fall through to the
        # input branch, whose dependency lookup would over-match via the parent
        # directory and pull in unrelated overlays.
        is_target = any(_path_selects_overlay(leaf, norm) for leaf in all_leaves)
        if is_target:
            matched = [o for o in affected if _path_selects_overlay(o.path, norm)]
        else:
            fed = {o.path for o in graph.affected_overlays([norm])}
            matched = [o for o in affected if o.path in fed]

        if matched:
            for o in matched:
                selected[o.path] = o
        elif (repo_root / norm).exists():
            warnings.append(f"path '{norm}' selected no overlay with drift against the baseline")

    ordered = [o for o in affected if o.path in selected]
    return ordered, warnings


def _open_external_worktrees(stack: contextlib.ExitStack, plan: _ExternalPlan) -> dict[Path, Path]:
    """Create one worktree per pinnable external repo at its HEAD, managed by ``stack``.

    All worktrees are created before any render and torn down together by the
    ExitStack, even on a mid-setup exception. If a worktree cannot be created
    (e.g. a repo that passed the read-only heuristic but still refuses), the repo
    is demoted to un-pinnable in ``plan`` so its overlays skip the rewrite and are
    not cached — never a crash, never a stale cached baseline.
    """
    b_worktrees: dict[Path, Path] = {}
    failed: set[Path] = set()
    for b_root, b_head in list(plan.repos.items()):
        try:
            wt = stack.enter_context(git.Worktree(b_head, repo_root=b_root))
        except git.GitError:
            log.warning("external_worktree_failed", repo=str(b_root))
            failed.add(b_root)
            continue
        b_worktrees[b_root] = wt.path
    if failed:
        _demote_failed_repos(plan, failed)
    return b_worktrees


def _demote_failed_repos(plan: _ExternalPlan, failed: set[Path]) -> None:
    """Move external repos whose worktree could not be created to un-pinnable."""
    for b_root in failed:
        plan.repos.pop(b_root, None)
    for kust, dep in list(plan.pinnable.items()):
        if dep.b_root in failed:
            plan.pinnable.pop(kust)
            plan.unpinnable.setdefault(kust, "external worktree could not be created")


def _overlay_external(
    overlay: models.Overlay,
    ext_by_leaf: dict[Path, list[models.ExternalChartRef]],
    plan: _ExternalPlan,
) -> tuple[list[str], bool]:
    """(external cache-key entries, cacheable) for one overlay.

    Not cacheable when the overlay has any un-pinnable external ref (baseline not
    diffed) OR any ref whose deps were resolved from the working tree
    (``deps_from_worktree``): both read live external state that cannot be keyed,
    so caching would fabricate drift on a later run.
    """
    refs = ext_by_leaf.get(overlay.path, [])
    cacheable = not any(
        r.declaring_kust in plan.unpinnable or r.declaring_kust in plan.deps_from_worktree for r in refs
    )
    return _external_cache_key(refs, plan), cacheable


def _diff_working_tree_vs_ref(  # noqa: PLR0913
    affected: list[models.Overlay],
    ctx: _RenderContext,
    resolved_ref: str,
    overlay_results: list[models.OverlayResult],
    plan: _ExternalPlan,
    ext_by_leaf: dict[Path, list[models.ExternalChartRef]],
) -> None:
    """Compare working tree against a baseline ref.

    Current renders read the live tree (A + external charts live). Baselines
    render from an A worktree at ``resolved_ref`` whose ``chartHome`` is redirected
    to a worktree of each pinnable external repo at its HEAD, so external drift is
    visible.
    """
    candidate_results = render.render_overlays_parallel(affected, ctx.repo_root, ctx.args, env=ctx.env)

    with contextlib.ExitStack() as stack:
        wt = stack.enter_context(git.Worktree(resolved_ref, ctx.repo_root))
        b_worktrees = _open_external_worktrees(stack, plan)
        if b_worktrees:
            _demote_unrewritten(plan, _rewrite_chart_homes(wt.path, plan, b_worktrees))
            _resolve_external_deps(plan, b_worktrees, ctx.args, ctx.env)

        for overlay, cand_result in zip(affected, candidate_results, strict=True):
            if not cand_result.success:
                overlay_results.append(
                    models.OverlayResult(path=overlay.path, error=f"candidate build failed: {cand_result.error}")
                )
                continue

            ext_key, cacheable = _overlay_external(overlay, ext_by_leaf, plan)
            baseline = _render_with_cache(overlay, wt.path, ctx, resolved_ref, ext_key, cacheable)
            if not baseline.success:
                overlay_results.append(
                    models.OverlayResult(
                        path=overlay.path, error=_baseline_failure_error(baseline, overlay, plan, ext_by_leaf)
                    )
                )
                continue

            overlay_results.append(diff.diff_rendered(baseline.output, cand_result.output, overlay.path))


def _diff_ref_vs_ref(  # noqa: PLR0913
    affected: list[models.Overlay],
    ctx: _RenderContext,
    resolved_base: str,
    resolved_target: str,
    overlay_results: list[models.OverlayResult],
    plan: _ExternalPlan,
    ext_by_leaf: dict[Path, list[models.ExternalChartRef]],
) -> None:
    """Compare two committed states using worktrees for both.

    External charts have no ref in A's history, so both sides pin to the external
    HEAD (both A worktrees get the same chartHome rewrite). The external
    contribution is then identical on both sides — no spurious external drift for
    a two-committed-A comparison — and the cached target render is keyed with the
    external SHAs so a later working-tree run cannot get a poisoned hit.
    """
    with contextlib.ExitStack() as stack:
        base_wt = stack.enter_context(git.Worktree(resolved_base, ctx.repo_root))
        target_wt = stack.enter_context(git.Worktree(resolved_target, ctx.repo_root))
        b_worktrees = _open_external_worktrees(stack, plan)
        if b_worktrees:
            rewritten = _rewrite_chart_homes(base_wt.path, plan, b_worktrees) & _rewrite_chart_homes(
                target_wt.path, plan, b_worktrees
            )
            _demote_unrewritten(plan, rewritten)
            _resolve_external_deps(plan, b_worktrees, ctx.args, ctx.env)

        for overlay in affected:
            ext_key, cacheable = _overlay_external(overlay, ext_by_leaf, plan)
            baseline = _render_with_cache(overlay, base_wt.path, ctx, resolved_base, ext_key, cacheable)
            if not baseline.success:
                overlay_results.append(
                    models.OverlayResult(
                        path=overlay.path, error=_baseline_failure_error(baseline, overlay, plan, ext_by_leaf)
                    )
                )
                continue

            target_result = render.render_overlay(
                overlay.path,
                target_wt.path / overlay.path,
                ctx.args,
                ctx.binary,
                ctx.env,
            )
            if not target_result.success:
                overlay_results.append(
                    models.OverlayResult(path=overlay.path, error=f"target build failed: {target_result.error}")
                )
                continue

            target_output = target_result.output
            if cacheable:
                key = render.cache_key(
                    resolved_target,
                    overlay.path,
                    ctx.args,
                    ctx.kust_ver,
                    ctx.env,
                    ext_key or None,
                    helm_ver=ctx.helm_ver if ext_key else None,
                )
                render.set_cached_render(key, target_output)

            overlay_results.append(diff.diff_rendered(baseline.output, target_output, overlay.path))


def _render_with_cache(  # noqa: PLR0913
    overlay: models.Overlay,
    worktree_root: Path,
    ctx: _RenderContext,
    resolved_ref: str,
    external: list[str] | None = None,
    cacheable: bool = True,
) -> models.RenderResult:
    """Render an overlay from a worktree, using the cache if available.

    ``cacheable`` is False for overlays with un-pinnable external sources: their
    baseline read a live external dir whose state is not in the key, so it must
    be neither served from nor written to the cache.

    Returns the ``RenderResult`` so callers can surface the underlying stderr on
    failure (a cache hit is returned as a successful result).
    """
    key = render.cache_key(
        resolved_ref,
        overlay.path,
        ctx.args,
        ctx.kust_ver,
        ctx.env,
        external or None,
        helm_ver=ctx.helm_ver if external else None,
    )
    if cacheable:
        cached = render.get_cached_render(key)
        if cached is not None:
            return models.RenderResult(overlay_path=overlay.path, output=cached, exit_code=0)

    result = render.render_overlay(
        overlay.path,
        worktree_root / overlay.path,
        ctx.args,
        ctx.binary,
        ctx.env,
    )
    if result.success and cacheable:
        render.set_cached_render(key, result.output)
    return result


def _baseline_failure_error(
    result: models.RenderResult,
    overlay: models.Overlay,
    plan: _ExternalPlan,
    ext_by_leaf: dict[Path, list[models.ExternalChartRef]],
) -> str:
    """Compose the error for a failed baseline render, surfacing the command stderr.

    When the overlay's external chart deps could not be resolved in the baseline
    worktree, name the chart(s) and label it a dependency problem (the stderr names
    the missing dependency); otherwise it is a genuine pre-existing chart break.
    """
    unresolved: list[str] = []
    for ref in ext_by_leaf.get(overlay.path, []):
        unresolved.extend(plan.deps_unresolved.get(ref.declaring_kust, ()))
    stderr = (result.error or "").strip()
    if unresolved:
        head = f"baseline build failed: helm deps unresolved in baseline worktree for chart(s) {', '.join(unresolved)}"
    else:
        head = "baseline build failed (pre-existing)"
    return f"{head}: {stderr}" if stderr else head
