"""Kustomize build orchestration with baseline caching.

Renders overlays by running `kustomize build` as a subprocess. Baseline
renders (committed state) are cached on disk; working tree renders are
never cached. Supports parallel rendering via ThreadPoolExecutor.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from kdrift import models

log: structlog.stdlib.BoundLogger = structlog.get_logger()

DEFAULT_KUSTOMIZE_ARGS: list[str] = [
    "--enable-helm",
    "--load-restrictor",
    "LoadRestrictionsNone",
]


class RenderError(Exception):
    """Raised when kustomize build fails."""


def find_kustomize() -> str:
    """Find the kustomize binary on PATH."""
    path = shutil.which("kustomize")
    if path is None:
        msg = "kustomize binary not found on PATH"
        raise RenderError(msg)
    return path


def kustomize_version(binary: str | None = None) -> str:
    """Get the kustomize version string."""
    binary = binary or find_kustomize()
    try:
        result = subprocess.run(
            [binary, "version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return result.stdout.strip()


def render_overlay(
    overlay_path: Path,
    build_dir: Path,
    kustomize_args: list[str] | None = None,
    binary: str | None = None,
) -> models.RenderResult:
    """Run kustomize build on an overlay directory.

    Args:
        overlay_path: Relative path to the overlay (for logging/identification).
        build_dir: Absolute path to the directory to build (may be in a worktree).
        kustomize_args: Extra args for kustomize build.
        binary: Path to kustomize binary.

    Returns:
        RenderResult with the rendered YAML or error details.
    """
    binary = binary or find_kustomize()
    args = kustomize_args if kustomize_args is not None else DEFAULT_KUSTOMIZE_ARGS

    cmd = [binary, "build", *args, str(build_dir)]
    log.debug("kustomize_build", overlay=str(overlay_path), cmd=" ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return models.RenderResult(
            overlay_path=overlay_path,
            error="kustomize binary not found on PATH",
            exit_code=127,
        )

    if result.returncode != 0:
        return models.RenderResult(
            overlay_path=overlay_path,
            error=result.stderr.strip(),
            exit_code=result.returncode,
        )

    return models.RenderResult(
        overlay_path=overlay_path,
        output=result.stdout,
        exit_code=0,
    )


def render_overlays_parallel(
    overlays: list[models.Overlay],
    build_root: Path,
    kustomize_args: list[str] | None = None,
    binary: str | None = None,
    max_workers: int | None = None,
) -> list[models.RenderResult]:
    """Render multiple overlays in parallel.

    Args:
        overlays: List of overlays to render.
        build_root: Root directory where overlays can be found.
        kustomize_args: Extra args for kustomize build.
        binary: Path to kustomize binary.
        max_workers: Max thread pool workers (defaults to min(len(overlays), 8)).

    Returns:
        List of RenderResults, one per overlay, preserving input order.
    """
    if not overlays:
        return []

    workers = max_workers or min(len(overlays), 8)
    results: dict[Path, models.RenderResult] = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                render_overlay,
                overlay.path,
                build_root / overlay.path,
                kustomize_args,
                binary,
            ): overlay
            for overlay in overlays
        }
        for future in as_completed(futures):
            overlay = futures[future]
            results[overlay.path] = future.result()

    return [results[o.path] for o in overlays]


def cache_key(
    ref: str,
    overlay_path: Path,
    kustomize_args: list[str],
    kustomize_ver: str,
) -> str:
    """Compute a cache key for a baseline render."""
    parts = [ref, str(overlay_path), kustomize_ver, *kustomize_args]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def cache_dir() -> Path:
    """Get the cache directory for baseline renders."""
    xdg = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    d = Path(xdg) / "kdrift"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cached_render(key: str) -> str | None:
    """Get a cached baseline render, or None if not cached."""
    path = cache_dir() / f"{key}.yaml"
    if path.is_file():
        return path.read_text()
    return None


def set_cached_render(key: str, content: str) -> None:
    """Cache a baseline render."""
    path = cache_dir() / f"{key}.yaml"
    path.write_text(content)
