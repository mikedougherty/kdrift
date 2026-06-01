"""Shared render-diff orchestration for all frontends.

Single function that goes from changed files to structured diff results.
Called by CLI, watch mode, and future MCP/LSP servers.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from kdrift import diff, discover, git, models, render

log: structlog.stdlib.BoundLogger = structlog.get_logger()


def run_diff(
    repo_root: Path,
    ref: str = "HEAD",
    paths: list[Path] | None = None,
    overlay_filter: Path | None = None,
    kustomize_args: list[str] | None = None,
) -> models.DiffResult:
    """Run the full discover -> render -> diff pipeline.

    Args:
        repo_root: Git repository root.
        ref: Git ref for baseline comparison.
        paths: Scope changed-file detection to these paths.
        overlay_filter: Only diff this specific overlay.
        kustomize_args: Override kustomize build flags.

    Returns:
        DiffResult with per-overlay, per-resource changes.
    """
    args = kustomize_args if kustomize_args is not None else render.DEFAULT_KUSTOMIZE_ARGS

    resolved_ref = git.resolve_ref(ref, repo_root)
    short_ref = git.get_short_sha(ref, repo_root)

    changed = git.changed_files(ref, paths, repo_root)
    if not changed:
        log.info("no_changes_detected", ref=short_ref)
        return models.DiffResult(ref=short_ref)

    log.debug("changed_files", count=len(changed), ref=short_ref)

    graph = discover.DependencyGraph(repo_root)
    graph.build()

    if overlay_filter is not None:
        kust = discover._find_kustomization_in(repo_root / overlay_filter)
        if kust is None:
            return models.DiffResult(
                ref=short_ref,
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
        log.info("no_affected_overlays", ref=short_ref)
        return models.DiffResult(ref=short_ref)

    log.info("affected_overlays", count=len(affected), overlays=[str(o.path) for o in affected])

    candidate_results = render.render_overlays_parallel(affected, repo_root, args)

    binary = render.find_kustomize()
    kust_ver = render.kustomize_version(binary)

    overlay_results: list[models.OverlayResult] = []
    errors: list[str] = []

    with git.Worktree(resolved_ref, repo_root) as wt:
        for overlay, cand_result in zip(affected, candidate_results, strict=True):
            if not cand_result.success:
                overlay_results.append(
                    models.OverlayResult(
                        path=overlay.path,
                        error=f"candidate build failed: {cand_result.error}",
                    )
                )
                continue

            key = render.cache_key(resolved_ref, overlay.path, args, kust_ver)
            cached = render.get_cached_render(key)

            if cached is not None:
                baseline_output = cached
            else:
                baseline_result = render.render_overlay(
                    overlay.path,
                    wt.path / overlay.path,
                    args,
                    binary,
                )
                if not baseline_result.success:
                    overlay_results.append(
                        models.OverlayResult(
                            path=overlay.path,
                            error=f"baseline build failed (pre-existing): {baseline_result.error}",
                        )
                    )
                    continue
                baseline_output = baseline_result.output
                render.set_cached_render(key, baseline_output)

            overlay_result = diff.diff_rendered(
                baseline_output,
                cand_result.output,
                overlay.path,
            )
            overlay_results.append(overlay_result)

    return models.DiffResult(
        ref=short_ref,
        overlays=overlay_results,
        errors=errors,
    )
