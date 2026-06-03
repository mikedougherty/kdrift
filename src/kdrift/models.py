"""Pydantic models for kdrift structured output."""

from __future__ import annotations

import enum
from pathlib import Path

import pydantic


class ResourceId(pydantic.BaseModel):
    """Unique identifier for a Kubernetes resource."""

    model_config = pydantic.ConfigDict(frozen=True)

    group: str = ""
    version: str = "v1"
    kind: str
    namespace: str = ""
    name: str

    @property
    def gvk(self) -> str:
        """Group/Version/Kind string (e.g. 'apps/v1/Deployment')."""
        if self.group:
            return f"{self.group}/{self.version}/{self.kind}"
        return f"{self.version}/{self.kind}"

    @classmethod
    def from_manifest(cls, manifest: dict[str, object]) -> ResourceId:
        """Extract resource identity from a parsed YAML document."""
        api_version = str(manifest.get("apiVersion", "v1"))
        kind = str(manifest.get("kind", ""))
        metadata = manifest.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        group = ""
        version = api_version
        if "/" in api_version:
            group, version = api_version.split("/", 1)

        return cls(
            group=group,
            version=version,
            kind=kind,
            namespace=str(metadata.get("namespace", "")),
            name=str(metadata.get("name", "")),
        )


class ResourceDiff(pydantic.BaseModel):
    """Diff result for a single Kubernetes resource."""

    resource_id: ResourceId
    diff_text: str
    lines_added: int = 0
    lines_removed: int = 0


class DiffStatus(enum.StrEnum):
    """Status of a resource comparison."""

    MODIFIED = "modified"
    ADDED = "added"
    REMOVED = "removed"


class ResourceChange(pydantic.BaseModel):
    """A changed resource with its diff and status."""

    resource_id: ResourceId
    status: DiffStatus
    diff_text: str = ""
    lines_added: int = 0
    lines_removed: int = 0


class OverlayResult(pydantic.BaseModel):
    """Diff result for a single overlay directory."""

    path: Path
    changes: list[ResourceChange] = pydantic.Field(default_factory=list)
    error: str | None = None

    @property
    def has_changes(self) -> bool:
        """Whether this overlay has any resource changes."""
        return len(self.changes) > 0

    @property
    def has_error(self) -> bool:
        """Whether rendering this overlay produced an error."""
        return self.error is not None


class DiffResult(pydantic.BaseModel):
    """Top-level result of a kdrift diff operation."""

    ref: str
    target_ref: str | None = None
    overlays: list[OverlayResult] = pydantic.Field(default_factory=list)
    errors: list[str] = pydantic.Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Whether any overlay has changes."""
        return any(o.has_changes for o in self.overlays)

    @property
    def has_errors(self) -> bool:
        """Whether any errors occurred."""
        return len(self.errors) > 0 or any(o.has_error for o in self.overlays)


class Overlay(pydantic.BaseModel):
    """A discovered kustomize overlay (leaf node in the dependency graph)."""

    model_config = pydantic.ConfigDict(frozen=True)

    path: Path
    kustomization_file: Path


class DependencyEdge(pydantic.BaseModel):
    """An edge in the kustomize dependency graph: file -> overlay that uses it."""

    model_config = pydantic.ConfigDict(frozen=True)

    source: Path
    target: Path


class RenderResult(pydantic.BaseModel):
    """Result of a kustomize build for a single overlay."""

    overlay_path: Path
    output: str = ""
    error: str | None = None
    exit_code: int = 0

    @property
    def success(self) -> bool:
        """Whether the build succeeded."""
        return self.exit_code == 0
