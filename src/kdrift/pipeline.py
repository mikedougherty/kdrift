"""Shared render-diff orchestration for all frontends.

Single function that goes from changed files to structured diff results.
Called by CLI, watch mode, and future MCP/LSP servers.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import structlog

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
        paths: Scope changed-file detection to these paths.
        overlay_filter: Only diff this specific overlay.
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

    if target_ref is not None:
        changed = git.changed_files_between(ref, target_ref, paths, repo_root)
    else:
        changed = git.changed_files(ref, paths, repo_root)

    if not changed:
        log.info("no_changes_detected", ref=short_ref, target_ref=short_target)
        return models.DiffResult(ref=short_ref, target_ref=short_target)

    log.debug("changed_files", count=len(changed), ref=short_ref, target_ref=short_target)

    graph = discover.DependencyGraph(repo_root)
    graph.build()

    if overlay_filter is not None:
        kust = discover._find_kustomization_in(repo_root / overlay_filter)
        if kust is None:
            return models.DiffResult(
                ref=short_ref,
                target_ref=short_target,
                errors=[f"No kustomization.yaml found in {overlay_filter}"],
            )
        affected = [
            models.Overlay(
                path=overlay_filter,
                kustomization_file=kust.relative_to(repo_root),
            )
        ]
    else:
        affected = graph.affected_overlays(changed)

    if not affected:
        log.info("no_affected_overlays", ref=short_ref, target_ref=short_target)
        return models.DiffResult(ref=short_ref, target_ref=short_target)

    log.info("affected_overlays", count=len(affected), overlays=[str(o.path) for o in affected])

    ctx = _RenderContext(
        repo_root=repo_root,
        args=args,
        binary=render.find_kustomize(),
        kust_ver=render.kustomize_version(),
        env=kustomize_env,
    )

    overlay_results: list[models.OverlayResult] = []
    errors: list[str] = []

    if target_ref is not None:
        assert resolved_target is not None
        _diff_ref_vs_ref(affected, ctx, resolved_ref, resolved_target, overlay_results)
    else:
        _diff_working_tree_vs_ref(affected, ctx, resolved_ref, overlay_results)

    return models.DiffResult(
        ref=short_ref,
        target_ref=short_target,
        overlays=overlay_results,
        errors=errors,
    )


def _diff_working_tree_vs_ref(
    affected: list[models.Overlay],
    ctx: _RenderContext,
    resolved_ref: str,
    overlay_results: list[models.OverlayResult],
) -> None:
    """Compare working tree against a baseline ref."""
    candidate_results = render.render_overlays_parallel(affected, ctx.repo_root, ctx.args, env=ctx.env)

    with git.Worktree(resolved_ref, ctx.repo_root) as wt:
        for overlay, cand_result in zip(affected, candidate_results, strict=True):
            if not cand_result.success:
                overlay_results.append(
                    models.OverlayResult(
                        path=overlay.path,
                        error=f"candidate build failed: {cand_result.error}",
                    )
                )
                continue

            baseline_output = _render_with_cache(overlay, wt.path, ctx, resolved_ref)
            if baseline_output is None:
                overlay_results.append(
                    models.OverlayResult(
                        path=overlay.path,
                        error="baseline build failed (pre-existing)",
                    )
                )
                continue

            overlay_results.append(diff.diff_rendered(baseline_output, cand_result.output, overlay.path))


def _diff_ref_vs_ref(
    affected: list[models.Overlay],
    ctx: _RenderContext,
    resolved_base: str,
    resolved_target: str,
    overlay_results: list[models.OverlayResult],
) -> None:
    """Compare two committed states using worktrees for both."""
    with (
        git.Worktree(resolved_base, ctx.repo_root) as base_wt,
        git.Worktree(resolved_target, ctx.repo_root) as target_wt,
    ):
        for overlay in affected:
            baseline_output = _render_with_cache(overlay, base_wt.path, ctx, resolved_base)
            if baseline_output is None:
                overlay_results.append(
                    models.OverlayResult(
                        path=overlay.path,
                        error="baseline build failed (pre-existing)",
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
                    models.OverlayResult(
                        path=overlay.path,
                        error=f"target build failed: {target_result.error}",
                    )
                )
                continue

            target_output = target_result.output
            key = render.cache_key(resolved_target, overlay.path, ctx.args, ctx.kust_ver, ctx.env)
            render.set_cached_render(key, target_output)

            overlay_results.append(diff.diff_rendered(baseline_output, target_output, overlay.path))


def _render_with_cache(
    overlay: models.Overlay,
    worktree_root: Path,
    ctx: _RenderContext,
    resolved_ref: str,
) -> str | None:
    """Render an overlay from a worktree, using the cache if available."""
    key = render.cache_key(resolved_ref, overlay.path, ctx.args, ctx.kust_ver, ctx.env)
    cached = render.get_cached_render(key)
    if cached is not None:
        return cached

    result = render.render_overlay(
        overlay.path,
        worktree_root / overlay.path,
        ctx.args,
        ctx.binary,
        ctx.env,
    )
    if not result.success:
        return None

    render.set_cached_render(key, result.output)
    return result.output
