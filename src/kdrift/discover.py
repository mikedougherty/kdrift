"""Overlay discovery and dependency graph construction.

Parses all kustomization.yaml files in a repository, builds a reverse
dependency DAG (file -> overlays that reference it), and identifies
leaf overlays (nodes with no incoming edges). Entry point is git status,
not directory walking: changed files are mapped through the dependency
graph to find affected overlays.
"""

from __future__ import annotations

import dataclasses
import os.path
from pathlib import Path

import structlog
import yaml

from kdrift import models, safe_loader

log: structlog.stdlib.BoundLogger = structlog.get_logger()

KUSTOMIZATION_FILENAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")


class DiscoveryError(Exception):
    """Raised when overlay discovery encounters an unrecoverable error."""


@dataclasses.dataclass(frozen=True)
class _KustRefs:
    """References extracted from a single kustomization.yaml.

    ``files`` are individual file/directory paths (repo-relative) matched
    exactly or by ancestor directory, as before. ``subtrees`` are directories
    whose *entire contents* are inputs (helm chart directories): any file
    changed anywhere beneath them affects the overlay. A subtree is
    repo-relative when it resolves inside the repo, or absolute when it points
    at an out-of-repo checkout (e.g. an absolute ``helmGlobals.chartHome``).
    """

    files: list[Path]
    subtrees: list[Path]
    external_home: Path | None = None


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
        self._subtree_to_overlays: dict[Path, set[Path]] = {}
        self._external_chart_refs: list[models.ExternalChartRef] = []
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

            for ref_path in refs.files:
                self._file_to_overlays.setdefault(ref_path, set()).add(overlay_dir)

                ref_kust = _find_kustomization_in(self.repo_root / ref_path)
                if ref_kust is not None:
                    ref_overlay = ref_path
                    self._parent_of.setdefault(ref_overlay, set()).add(overlay_dir)

            for subtree in refs.subtrees:
                self._subtree_to_overlays.setdefault(subtree, set()).add(overlay_dir)

            if refs.external_home is not None:
                self._external_chart_refs.append(
                    models.ExternalChartRef(declaring_kust=overlay_dir, chart_home_abs=refs.external_home)
                )

        self._build_dir_index()
        self._built = True
        log.debug(
            "dependency_graph_built",
            overlays=len(self._overlay_dirs),
            files_tracked=len(self._file_to_overlays),
            subtrees_tracked=len(self._subtree_to_overlays),
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

        for raw in changed_files:
            changed = self._normalize_changed(raw)

            if changed in self._file_to_overlays:
                affected_dirs.update(self._file_to_overlays[changed])

            changed_dir = changed.parent
            if changed_dir in self._dir_to_overlays:
                affected_dirs.update(self._dir_to_overlays[changed_dir])

            if changed in self._dir_to_overlays:
                affected_dirs.update(self._dir_to_overlays[changed])

            affected_dirs.update(self._subtree_overlays_for(changed))

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

    def _normalize_changed(self, raw: Path) -> Path:
        """Normalize a changed-file path into the graph's key space.

        Graph keys are repo-relative for in-repo inputs and absolute for
        out-of-repo subtree watches. An absolute path inside the repo becomes
        its repo-relative form so it matches the file/dir indexes and in-repo
        chart subtrees; an absolute path outside the repo is resolved and kept
        absolute so it matches an out-of-repo chart subtree (following a
        symlink and normalizing ``/tmp`` vs ``/private/tmp``). Relative paths,
        as git reports them, pass through unchanged.
        """
        if not raw.is_absolute():
            return raw
        try:
            resolved = raw.resolve()
        except OSError:
            resolved = raw
        try:
            return resolved.relative_to(self.repo_root.resolve())
        except (ValueError, OSError):
            return resolved

    def _subtree_overlays_for(self, changed: Path) -> set[Path]:
        """Overlays whose watched subtree (a chart dir) contains ``changed``.

        ``changed`` is already normalized into the graph key space, so an
        in-repo path is repo-relative (matches a relative chart-dir key) and an
        out-of-repo path is absolute (matches an absolute key). Matches when any
        ancestor directory of the changed path is a watched subtree.
        """
        if not self._subtree_to_overlays:
            return set()
        result: set[Path] = set()
        for ancestor in (changed, *changed.parents):
            overlays = self._subtree_to_overlays.get(ancestor)
            if overlays is not None:
                result.update(overlays)
        return result

    def external_chart_refs(self) -> list[models.ExternalChartRef]:
        """External helm chart declarations (declaring kustomization + chart home).

        Each entry is a kustomization whose ``helmGlobals.chartHome`` resolves to
        an out-of-repo directory. The pipeline uses these to redirect the baseline
        render at a worktree of the owning repo. See ``external_sources`` for the
        chart directories used in change matching.
        """
        self._ensure_built()
        return list(self._external_chart_refs)

    def _referencing_closure(self, start: Path) -> set[Path]:
        """All overlays that include ``start`` (itself + transitive referencers)."""
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for parent in self._parent_of.get(cur, ()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def external_refs_for(self, overlays: list[models.Overlay]) -> dict[Path, list[models.ExternalChartRef]]:
        """Map each given overlay to the external chart refs it includes.

        An overlay includes a ref when the ref's declaring kustomization is the
        overlay itself or a base it composes. Matching is by ancestry (the
        referencing closure of the declaring kustomization), not leaf resolution,
        so a forced non-leaf overlay (``--overlay base``) still gets its refs.
        """
        self._ensure_built()
        wanted = {o.path for o in overlays}
        result: dict[Path, list[models.ExternalChartRef]] = {}
        for ref in self._external_chart_refs:
            for overlay in self._referencing_closure(ref.declaring_kust) & wanted:
                result.setdefault(overlay, []).append(ref)
        return result

    def external_sources(self, overlays: list[models.Overlay]) -> list[Path]:
        """Absolute out-of-repo source dirs the given leaf overlays depend on.

        Used to warn that a diff cannot capture drift originating outside the
        analyzed repository (an absolute ``helmGlobals.chartHome`` or a symlink
        escaping the repo). Returns the external chart directories whose
        declaring overlays resolve to any of ``overlays``.
        """
        self._ensure_built()
        affected = {o.path for o in overlays}
        leaves = {o.path for o in self.leaf_overlays}
        result: set[Path] = set()
        for subtree, decl_overlays in self._subtree_to_overlays.items():
            if not subtree.is_absolute():
                continue
            if self._resolve_to_leaves(decl_overlays, leaves) & affected:
                result.add(subtree)
        return sorted(result)

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


def _parse_references(kust_file: Path, repo_root: Path) -> _KustRefs:
    """Extract all file/directory and subtree references from a kustomization.yaml."""
    with kust_file.open() as f:
        data = yaml.load(f, Loader=safe_loader)

    if not isinstance(data, dict):
        return _KustRefs(files=[], subtrees=[])

    kust_dir = kust_file.parent.relative_to(repo_root)
    files: list[Path] = []

    files.extend(_collect_string_list_refs(data, kust_dir))
    files.extend(_collect_patch_refs(data, kust_dir))
    files.extend(_collect_generator_refs(data, kust_dir))
    values_files, subtrees, external_home = _collect_helm_refs(data, kust_dir, repo_root)
    files.extend(values_files)
    files.extend(_collect_replacement_refs(data, kust_dir))
    files.extend(_collect_openapi_refs(data, kust_dir))

    return _KustRefs(files=files, subtrees=subtrees, external_home=external_home)


def _collect_string_list_refs(data: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect refs from simple string-list fields (resources, components, bases, etc.).

    Covers every kustomize field whose value is a list of local file/dir paths:
    resources/components/bases (the composition graph), the deprecated
    patchesStrategicMerge, and the plugin/config lists generators, transformers,
    configurations, and crds.
    """
    refs: list[Path] = []
    string_list_fields = (
        "resources",
        "components",
        "bases",
        "patchesStrategicMerge",
        "generators",
        "transformers",
        "configurations",
        "crds",
    )
    for field in string_list_fields:
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
            # `env` (singular) is the deprecated scalar form of `envs`.
            env = gen.get("env")
            if isinstance(env, str):
                refs.append(_resolve_ref_path(kust_dir, env))
    return refs


DEFAULT_CHART_HOME = "charts"


def _collect_helm_refs(
    data: dict[str, object], kust_dir: Path, repo_root: Path
) -> tuple[list[Path], list[Path], Path | None]:
    """Collect helmCharts inputs: values files, chart directories, external home.

    Returns ``(values_files, chart_subtrees, external_home)``. Values files are
    ordinary repo-relative file refs. Each chart directory (``<chartHome>/<name>``,
    ``chartHome`` defaulting to ``charts``) is a subtree watch: editing any file
    inside a local chart changes the rendered output. The chart directory is
    resolved so a symlinked chart is followed; one resolving inside the repo is
    returned repo-relative, one resolving outside (absolute ``chartHome``, an
    escaping symlink, or a ``../`` escape) is returned absolute. ``external_home``
    is the resolved absolute ``chartHome`` directory when it escapes the repo and
    at least one named chart uses it (the directory the baseline render must be
    redirected away from), else ``None``.
    """
    values_files: list[Path] = []
    subtrees: list[Path] = []
    helm_charts = data.get("helmCharts", [])
    if not isinstance(helm_charts, list):
        return values_files, subtrees, None

    chart_home = _chart_home(data)
    home_abs, is_external = _resolve_chart_home(repo_root, kust_dir, chart_home)
    has_named_chart = False

    for chart in helm_charts:
        if not isinstance(chart, dict):
            continue
        values_files.extend(_collect_chart_values(chart, kust_dir))
        name = chart.get("name")
        if isinstance(name, str) and name:
            has_named_chart = True
            subtree = _resolve_chart_dir(repo_root, kust_dir, chart_home, name)
            if subtree is not None:
                subtrees.append(subtree)

    external_home = home_abs if (is_external and has_named_chart) else None
    return values_files, subtrees, external_home


def _chart_home(data: dict[str, object]) -> str:
    """Read helmGlobals.chartHome, defaulting to 'charts'."""
    globals_field = data.get("helmGlobals", {})
    if isinstance(globals_field, dict):
        raw_home = globals_field.get("chartHome")
        if isinstance(raw_home, str) and raw_home:
            return raw_home
    return DEFAULT_CHART_HOME


def _collect_chart_values(chart: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect valuesFile and additionalValuesFiles refs for one chart."""
    refs: list[Path] = []
    values_list = chart.get("additionalValuesFiles", [])
    if isinstance(values_list, list):
        for vf in values_list:
            if isinstance(vf, str):
                refs.append(_resolve_ref_path(kust_dir, vf))
    values_file = chart.get("valuesFile")
    if isinstance(values_file, str):
        refs.append(_resolve_ref_path(kust_dir, values_file))
    return refs


def _resolve_chart_home(repo_root: Path, kust_dir: Path, chart_home: str) -> tuple[Path | None, bool]:
    """Resolve ``chartHome`` to ``(absolute_dir, is_external)``.

    Returns ``(None, False)`` for a remote ``chartHome`` we do not track.
    ``is_external`` is True when the resolved directory escapes the repo (an
    absolute value, an escaping symlink, or a ``../`` escape).
    """
    if _is_remote_ref(chart_home):
        return None, False

    home = Path(chart_home)
    raw = home if home.is_absolute() else repo_root / kust_dir / home

    try:
        real = raw.resolve()
        repo_real = repo_root.resolve()
    except OSError:
        return None, False

    try:
        real.relative_to(repo_real)
    except ValueError:
        return real, True
    return real, False


def _resolve_chart_dir(repo_root: Path, kust_dir: Path, chart_home: str, name: str) -> Path | None:
    """Resolve a helm chart directory to a subtree-watch path.

    Resolves the full ``<chartHome>/<name>`` path (following symlinks at any level,
    including a symlinked individual chart under an in-repo chartHome). Returns a
    repo-relative path when the chart resolves inside the repo, an absolute path
    when it points outside (absolute ``chartHome``, an escaping symlink at either
    level, or a ``../`` escape), or ``None`` for a remote ``chartHome``.
    """
    if _is_remote_ref(chart_home):
        return None

    home = Path(chart_home)
    raw = home / name if home.is_absolute() else repo_root / kust_dir / home / name

    try:
        real = raw.resolve()
        repo_real = repo_root.resolve()
    except OSError:
        return None

    try:
        return real.relative_to(repo_real)
    except ValueError:
        return real


def _collect_openapi_refs(data: dict[str, object], kust_dir: Path) -> list[Path]:
    """Collect the custom schema path from the openapi field."""
    openapi = data.get("openapi")
    if isinstance(openapi, dict):
        path = openapi.get("path")
        if isinstance(path, str) and not _is_remote_ref(path):
            return [_resolve_ref_path(kust_dir, path)]
    return []


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
