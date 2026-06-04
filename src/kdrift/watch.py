"""Filesystem watcher with dependency-aware rebuilds.

Monitors the repository for file changes, consults the dependency graph
to identify affected overlays, and re-runs the diff pipeline. Debounces
rapid saves (~300ms) so editor auto-save doesn't trigger multiple rebuilds.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog
import watchfiles

from kdrift import models, pipeline

log: structlog.stdlib.BoundLogger = structlog.get_logger()

DEBOUNCE_MS = 300


def watch(  # noqa: PLR0913
    repo_root: Path,
    ref: str = "HEAD",
    paths: list[Path] | None = None,
    output_format: str = "unified",
    kustomize_args: list[str] | None = None,
    kustomize_env: dict[str, str] | None = None,
) -> None:
    """Watch for file changes and continuously diff affected overlays.

    Args:
        repo_root: Git repository root.
        ref: Git ref for baseline comparison (re-resolved on each rebuild).
        paths: Scope watching to these paths.
        output_format: "unified" or "json".
        kustomize_args: Override kustomize build flags.
        kustomize_env: Extra environment variables for kustomize subprocesses.
    """
    args = kustomize_args if kustomize_args is not None else None
    watch_paths = [str(repo_root / p) for p in paths] if paths else [str(repo_root)]

    log.info("watch_started", paths=watch_paths)
    _print_header(repo_root, ref)

    try:
        for changes in watchfiles.watch(
            *watch_paths,
            debounce=DEBOUNCE_MS,
            step=50,
            watch_filter=_kustomize_filter,
        ):
            changed_files = _extract_changed_files(changes, repo_root)
            if not changed_files:
                continue

            log.debug("changes_detected", files=[str(f) for f in changed_files])
            _run_and_print(repo_root, ref, paths, output_format, args, kustomize_env)
    except KeyboardInterrupt:
        log.info("watch_stopped")


def _kustomize_filter(change: watchfiles.Change, path: str) -> bool:
    """Filter to only watch relevant files (YAML, kustomization files)."""
    p = Path(path)
    if p.name.startswith("."):
        return False
    if p.suffix in (".yaml", ".yml", ".json", ".properties"):
        return True
    return p.name in ("kustomization.yaml", "kustomization.yml", "Kustomization")


def _extract_changed_files(
    changes: set[tuple[watchfiles.Change, str]],
    repo_root: Path,
) -> list[Path]:
    """Convert watchfiles change set to repo-relative paths."""
    result: list[Path] = []
    for _change_type, path_str in changes:
        try:
            rel = Path(path_str).relative_to(repo_root)
            result.append(rel)
        except ValueError:
            continue
    return result


def _run_and_print(  # noqa: PLR0913
    repo_root: Path,
    ref: str,
    paths: list[Path] | None,
    output_format: str,
    kustomize_args: list[str] | None,
    kustomize_env: dict[str, str] | None = None,
) -> None:
    """Run the diff pipeline and print results."""
    try:
        result = pipeline.run_diff(
            repo_root=repo_root,
            ref=ref,
            paths=paths,
            kustomize_args=kustomize_args,
            kustomize_env=kustomize_env,
        )
    except Exception:
        log.exception("pipeline_error")
        return

    if output_format == "json":
        _print_json(result)
    else:
        _print_unified(result)


def _print_header(repo_root: Path, ref: str) -> None:
    """Print the watch mode header."""
    print(f"\n{'=' * 60}")
    print(f"  kdrift watch | {repo_root.name} | baseline: {ref}")
    print("  Watching for changes... (Ctrl+C to stop)")
    print(f"{'=' * 60}\n")


def _print_json(result: models.DiffResult) -> None:
    """Print structured JSON output for a watch cycle."""
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ref": result.ref,
        "overlays": json.loads(result.model_dump_json())["overlays"],
    }
    print(json.dumps(output))


def _print_unified(result: models.DiffResult) -> None:
    """Print unified diff output for a watch cycle."""
    print(f"\n--- {time.strftime('%H:%M:%S')} ---")

    if not result.has_changes and not result.has_errors:
        print("  No drift detected.")
        return

    for overlay_result in result.overlays:
        if overlay_result.has_error:
            print(f"  ERROR [{overlay_result.path}]: {overlay_result.error}")
            continue

        if not overlay_result.has_changes:
            continue

        print(f"\n  === {overlay_result.path} ===")
        for change in overlay_result.changes:
            status_marker = {
                models.DiffStatus.ADDED: "[NEW]",
                models.DiffStatus.REMOVED: "[DEL]",
                models.DiffStatus.MODIFIED: "",
            }[change.status]

            rid = change.resource_id
            header = f"{rid.gvk} {rid.namespace}/{rid.name}" if rid.namespace else f"{rid.gvk} {rid.name}"
            if status_marker:
                header = f"{status_marker} {header}"
            print(f"  {header}")
            print(f"    +{change.lines_added} -{change.lines_removed}")

    for error in result.errors:
        print(f"  ERROR: {error}")
