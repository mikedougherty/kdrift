"""Per-resource structured diffs with two-phase matching.

Phase 1: exact match by GVK + namespace + name.
Phase 2: generator-aware matching for configMapGenerator/secretGenerator
hash-suffixed names, using longest-name-first ordering to prevent false
matches (e.g., dex-config-abc12 matching generator `dex` instead of
`dex-config`).
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import yaml

from kdrift import models

_SafeLoader: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_KUSTOMIZE_HASH_CHARS = "bcdfghjklmnpqrstvwxz2456789"
_HASH_SUFFIX_RE = re.compile(rf"^(.+)-[{_KUSTOMIZE_HASH_CHARS}]{{5,10}}$")


def diff_rendered(
    baseline: str,
    candidate: str,
    overlay_path: Path,
) -> models.OverlayResult:
    """Diff two rendered YAML strings and produce per-resource changes.

    Args:
        baseline: Rendered YAML from the baseline ref.
        candidate: Rendered YAML from the working tree.
        overlay_path: Path to the overlay (for identification).

    Returns:
        OverlayResult with per-resource changes.
    """
    baseline_resources = _parse_resources(baseline)
    candidate_resources = _parse_resources(candidate)

    changes = _match_and_diff(baseline_resources, candidate_resources)
    return models.OverlayResult(path=overlay_path, changes=changes)


def _parse_resources(rendered: str) -> dict[models.ResourceId, str]:
    """Split rendered YAML into individual resources keyed by identity."""
    resources: dict[models.ResourceId, str] = {}
    if not rendered.strip():
        return resources

    docs = rendered.split("\n---")
    for raw_doc in docs:
        doc = raw_doc.strip()
        if not doc or doc == "---":
            continue

        try:
            parsed = yaml.load(doc, Loader=_SafeLoader)
        except yaml.YAMLError:
            continue

        if not isinstance(parsed, dict) or "kind" not in parsed:
            continue

        resource_id = models.ResourceId.from_manifest(parsed)
        resources[resource_id] = doc

    return resources


def _match_and_diff(
    baseline: dict[models.ResourceId, str],
    candidate: dict[models.ResourceId, str],
) -> list[models.ResourceChange]:
    """Two-phase resource matching and diffing."""
    changes: list[models.ResourceChange] = []
    matched_baseline: set[models.ResourceId] = set()
    matched_candidate: set[models.ResourceId] = set()

    for rid, candidate_yaml in candidate.items():
        if rid in baseline:
            matched_baseline.add(rid)
            matched_candidate.add(rid)
            diff_text = _unified_diff(baseline[rid], candidate_yaml, rid)
            if diff_text:
                added, removed = _count_diff_lines(diff_text)
                changes.append(
                    models.ResourceChange(
                        resource_id=rid,
                        status=models.DiffStatus.MODIFIED,
                        diff_text=diff_text,
                        lines_added=added,
                        lines_removed=removed,
                    )
                )

    unmatched_baseline = {rid: y for rid, y in baseline.items() if rid not in matched_baseline}
    unmatched_candidate = {rid: y for rid, y in candidate.items() if rid not in matched_candidate}

    if unmatched_baseline and unmatched_candidate:
        gen_matches = _generator_aware_match(unmatched_baseline, unmatched_candidate)
        for b_rid, c_rid in gen_matches:
            matched_baseline.add(b_rid)
            matched_candidate.add(c_rid)
            diff_text = _unified_diff(baseline[b_rid], candidate[c_rid], c_rid)
            if diff_text:
                added, removed = _count_diff_lines(diff_text)
                changes.append(
                    models.ResourceChange(
                        resource_id=c_rid,
                        status=models.DiffStatus.MODIFIED,
                        diff_text=diff_text,
                        lines_added=added,
                        lines_removed=removed,
                    )
                )

    for rid in sorted(unmatched_candidate.keys() - matched_candidate, key=lambda r: r.name):
        changes.append(
            models.ResourceChange(
                resource_id=rid,
                status=models.DiffStatus.ADDED,
                diff_text=_added_diff(candidate[rid], rid),
                lines_added=len(candidate[rid].splitlines()),
            )
        )

    for rid in sorted(unmatched_baseline.keys() - matched_baseline, key=lambda r: r.name):
        changes.append(
            models.ResourceChange(
                resource_id=rid,
                status=models.DiffStatus.REMOVED,
                diff_text=_removed_diff(baseline[rid], rid),
                lines_removed=len(baseline[rid].splitlines()),
            )
        )

    return changes


def _generator_aware_match(
    baseline: dict[models.ResourceId, str],
    candidate: dict[models.ResourceId, str],
) -> list[tuple[models.ResourceId, models.ResourceId]]:
    """Phase 2: match resources with hash-suffixed names.

    Sorts by name length (longest first) to prevent short generator
    names from stealing matches. E.g., generator `dex-config` should
    match `dex-config-abc12` before generator `dex` gets a chance.
    """
    matches: list[tuple[models.ResourceId, models.ResourceId]] = []
    used_candidate: set[models.ResourceId] = set()

    baseline_sorted = sorted(baseline.keys(), key=lambda r: len(r.name), reverse=True)

    for b_rid in baseline_sorted:
        b_base = _strip_hash_suffix(b_rid.name)
        for c_rid in candidate:
            if c_rid in used_candidate:
                continue
            if c_rid.group != b_rid.group or c_rid.version != b_rid.version:
                continue
            if c_rid.kind != b_rid.kind or c_rid.namespace != b_rid.namespace:
                continue
            c_base = _strip_hash_suffix(c_rid.name)
            if b_base == c_base:
                matches.append((b_rid, c_rid))
                used_candidate.add(c_rid)
                break

    return matches


def _strip_hash_suffix(name: str) -> str:
    """Strip a kustomize hash suffix from a resource name."""
    match = _HASH_SUFFIX_RE.match(name)
    return match.group(1) if match else name


def _unified_diff(baseline_yaml: str, candidate_yaml: str, rid: models.ResourceId) -> str:
    """Produce a unified diff between two YAML strings."""
    if baseline_yaml and not baseline_yaml.endswith("\n"):
        baseline_yaml += "\n"
    if candidate_yaml and not candidate_yaml.endswith("\n"):
        candidate_yaml += "\n"
    baseline_lines = baseline_yaml.splitlines(keepends=True)
    candidate_lines = candidate_yaml.splitlines(keepends=True)

    diff = difflib.unified_diff(
        baseline_lines,
        candidate_lines,
        fromfile=f"baseline/{rid.gvk}/{rid.namespace}/{rid.name}",
        tofile=f"candidate/{rid.gvk}/{rid.namespace}/{rid.name}",
    )
    return "".join(diff)


def _added_diff(yaml_text: str, rid: models.ResourceId) -> str:
    """Produce a diff showing a newly added resource."""
    lines = yaml_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        [],
        lines,
        fromfile="/dev/null",
        tofile=f"candidate/{rid.gvk}/{rid.namespace}/{rid.name}",
    )
    return "".join(diff)


def _removed_diff(yaml_text: str, rid: models.ResourceId) -> str:
    """Produce a diff showing a removed resource."""
    lines = yaml_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        lines,
        [],
        fromfile=f"baseline/{rid.gvk}/{rid.namespace}/{rid.name}",
        tofile="/dev/null",
    )
    return "".join(diff)


def _count_diff_lines(diff_text: str) -> tuple[int, int]:
    """Count added and removed lines in a unified diff."""
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed
