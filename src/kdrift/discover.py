"""Overlay discovery and dependency graph construction.

Parses all kustomization.yaml files in a repository, builds a reverse
dependency DAG (file -> overlays that reference it), and identifies
leaf overlays (nodes with no incoming edges). Entry point is git status,
not directory walking: changed files are mapped through the dependency
graph to find affected overlays.
"""

from __future__ import annotations

import os.path
from pathlib import Path

import structlog
import yaml

from kdrift import models

log: structlog.stdlib.BoundLogger = structlog.get_logger()

KUSTOMIZATION_FILENAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")


class DiscoveryError(Exception):
    """Raised when overlay discovery encounters an unrecoverable error."""


class DependencyGraph:
    """Reverse dependency graph: file -> set of overlay directories that use it.

    The graph tracks which kustomization.yaml directories reference each
    file (directly or transitively through bases/components). Leaf overlays
    are directories that no other kustomization.yaml references.
    """

    def __init__(self, repo_root: Path) -> None:
        """Initialize with the repository root path."""
        self.repo_root = repo_root
        self._file_to_overlays: dict[Path, set[Path]] = {}
        self._dir_to_overlays: dict[Path, set[Path]] = {}
        self._overlay_dirs: set[Path] = set()
        self._parent_of: dict[Path, set[Path]] = {}
        self._leaf_overlays: list[models.Overlay] | None = None
        self._kust_file_cache: dict[Path, Path | None] = {}
        self._built = False

    def build(self) -> None:
        """Scan the repo for kustomization.yaml files and build the graph."""
        kust_files = _find_kustomization_files(self.repo_root)
        if not kust_files:
            log.info("no_kustomization_files_found", repo=str(self.repo_root))
            self._built = True
            return

        for kust_file in kust_files:
            overlay_dir = kust_file.parent.relative_to(self.repo_root)
            self._overlay_dirs.add(overlay_dir)
            self._kust_file_cache[overlay_dir] = kust_file

            try:
                refs = _parse_references(kust_file, self.repo_root)
            except yaml.YAMLError:
                log.warning("malformed_kustomization", path=str(kust_file))
                continue

            for ref_path in refs:
                self._file_to_overlays.setdefault(ref_path, set()).add(overlay_dir)

                ref_kust = _find_kustomization_in(self.repo_root / ref_path)
                if ref_kust is not None:
                    ref_overlay = ref_path
                    self._parent_of.setdefault(ref_overlay, set()).add(overlay_dir)

        self._build_dir_index()
        self._built = True
        log.debug(
            "dependency_graph_built",
            overlays=len(self._overlay_dirs),
            files_tracked=len(self._file_to_overlays),
        )

    def _build_dir_index(self) -> None:
        """Build a directory-to-overlays index for fast prefix lookups."""
        for file_path, overlay_dirs in self._file_to_overlays.items():
            parent = file_path.parent
            while str(parent) != ".":
                self._dir_to_overlays.setdefault(parent, set()).update(overlay_dirs)
                parent = parent.parent
            self._dir_to_overlays.setdefault(parent, set()).update(overlay_dirs)

    @property
    def leaf_overlays(self) -> list[models.Overlay]:
        """Overlays that no other overlay references (deployment targets)."""
        self._ensure_built()
        if self._leaf_overlays is not None:
            return self._leaf_overlays

        leaves: list[models.Overlay] = []
        for overlay_dir in sorted(self._overlay_dirs):
            if overlay_dir not in self._parent_of:
                kust = self._kust_file_cache.get(overlay_dir)
                if kust is not None:
                    leaves.append(
                        models.Overlay(
                            path=overlay_dir,
                            kustomization_file=kust.relative_to(self.repo_root),
                        )
                    )

        self._leaf_overlays = leaves
        return leaves

    def affected_overlays(self, changed_files: list[Path]) -> list[models.Overlay]:
        """Find leaf overlays affected by the given changed files."""
        self._ensure_built()
        affected_dirs: set[Path] = set()

        for changed in changed_files:
            if changed in self._file_to_overlays:
                affected_dirs.update(self._file_to_overlays[changed])

            changed_dir = changed.parent
            if changed_dir in self._dir_to_overlays:
                affected_dirs.update(self._dir_to_overlays[changed_dir])

            if changed in self._dir_to_overlays:
                affected_dirs.update(self._dir_to_overlays[changed])

            if _is_kustomization_file(changed):
                kust_dir = changed.parent
                if kust_dir in self._overlay_dirs:
                    affected_dirs.add(kust_dir)

        leaves = {o.path for o in self.leaf_overlays}
        result_dirs = self._resolve_to_leaves(affected_dirs, leaves)

        result: list[models.Overlay] = []
        for d in sorted(result_dirs):
            kust = self._kust_file_cache.get(d)
            if kust is not None:
                result.append(
                    models.Overlay(
                        path=d,
                        kustomization_file=kust.relative_to(self.repo_root),
                    )
                )

        return result

    def _resolve_to_leaves(self, dirs: set[Path], leaves: set[Path]) -> set[Path]:
        """Resolve a set of overlay dirs to their leaf descendants."""
        result: set[Path] = set()
        for d in dirs:
            if d in leaves:
                result.add(d)
            elif d in self._parent_of:
                children = self._parent_of[d]
                result.update(self._resolve_to_leaves(children, leaves))
            else:
                result.add(d)
        return result

    def _ensure_built(self) -> None:
        if not self._built:
            msg = "Call build() before querying the dependency graph"
            raise DiscoveryError(msg)


def _find_kustomization_files(repo_root: Path) -> list[Path]:
    """Find all kustomization.yaml files in the repository."""
    names = set(KUSTOMIZATION_FILENAMES)
    results: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname in names:
                results.append(Path(dirpath) / fname)
    return sorted(results)


def _find_kustomization_in(directory: Path) -> Path | None:
    """Find the kustomization file in a directory, if any."""
    for name in KUSTOMIZATION_FILENAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _is_kustomization_file(path: Path) -> bool:
    """Check if a path is a kustomization.yaml file."""
    return path.name in KUSTOMIZATION_FILENAMES


def _is_parent_of(parent: Path, child: Path) -> bool:
    """Check if parent is a parent directory of child."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return parent != child


def _parse_references(kust_file: Path, repo_root: Path) -> list[Path]:
    """Extract all file/directory references from a kustomization.yaml."""
    with kust_file.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return []

    kust_dir = kust_file.parent.relative_to(repo_root)
    refs: list[Path] = []

    refs.extend(_collect_string_list_refs(data, kust_dir))
    refs.extend(_collect_patch_refs(data, kust_dir))
    refs.extend(_collect_generator_refs(data, kust_dir))
    refs.extend(_collect_helm_refs(data, kust_dir))
    refs.extend(_collect_replacement_refs(data, kust_dir))

    return refs


def _collect_string_list_refs(data: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect refs from simple string-list fields (resources, components, bases, etc.)."""
    refs: list[Path] = []
    for field in ("resources", "components", "bases", "patchesStrategicMerge"):
        entries = data.get(field, [])
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str) and not _is_remote_ref(entry):
                    refs.append(_resolve_ref_path(kust_dir, entry))
    return refs


def _collect_patch_refs(data: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect refs from patches and patchesJson6902 fields."""
    refs: list[Path] = []
    for field in ("patches", "patchesJson6902"):
        patches = data.get(field, [])
        if not isinstance(patches, list):
            continue
        for patch in patches:
            if isinstance(patch, str) and not _is_remote_ref(patch):
                refs.append(_resolve_ref_path(kust_dir, patch))
            elif isinstance(patch, dict):
                path = patch.get("path")
                if isinstance(path, str) and not _is_remote_ref(path):
                    refs.append(_resolve_ref_path(kust_dir, path))
    return refs


def _collect_generator_refs(data: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect file refs from configMapGenerator and secretGenerator."""
    refs: list[Path] = []
    for gen_field in ("configMapGenerator", "secretGenerator"):
        generators = data.get(gen_field, [])
        if not isinstance(generators, list):
            continue
        for gen in generators:
            if not isinstance(gen, dict):
                continue
            for file_field in ("files", "envs"):
                files = gen.get(file_field, [])
                if not isinstance(files, list):
                    continue
                for f_entry in files:
                    if isinstance(f_entry, str):
                        file_path = f_entry.split("=", 1)[-1] if "=" in f_entry else f_entry
                        refs.append(_resolve_ref_path(kust_dir, file_path))
    return refs


def _collect_helm_refs(data: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect valuesFile refs from helmCharts."""
    refs: list[Path] = []
    helm_charts = data.get("helmCharts", [])
    if not isinstance(helm_charts, list):
        return refs
    for chart in helm_charts:
        if not isinstance(chart, dict):
            continue
        values_files = chart.get("additionalValuesFiles", [])
        if isinstance(values_files, list):
            for vf in values_files:
                if isinstance(vf, str):
                    refs.append(_resolve_ref_path(kust_dir, vf))
        values_file = chart.get("valuesFile")
        if isinstance(values_file, str):
            refs.append(_resolve_ref_path(kust_dir, values_file))
    return refs


def _collect_replacement_refs(data: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect path refs from replacements."""
    refs: list[Path] = []
    replacements = data.get("replacements", [])
    if not isinstance(replacements, list):
        return refs
    for repl in replacements:
        if isinstance(repl, dict):
            path = repl.get("path")
            if isinstance(path, str) and not _is_remote_ref(path):
                refs.append(_resolve_ref_path(kust_dir, path))
    return refs


def _resolve_ref_path(kust_dir: Path, ref: str) -> Path:
    """Resolve a reference path relative to its kustomization.yaml directory."""
    raw = kust_dir / ref
    return Path(os.path.normpath(str(raw)))


def _is_remote_ref(ref: str) -> bool:
    """Check if a reference is remote (URL or git ref)."""
    return ref.startswith(("http://", "https://", "ssh://", "git@"))
