"""MCP server exposing kdrift tools for AI agents.

Run via: kdrift mcp
Configure in Claude Code's claude_desktop_config.json or .claude.json:
  {"mcpServers": {"kdrift": {"command": "kdrift", "args": ["mcp"]}}}
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from kdrift import config, discover, git, pipeline, render

server = FastMCP(
    name="kdrift",
    instructions=(
        "Kustomize manifest drift detection. Use kdrift_diff to check how your "
        "kustomize changes affect rendered manifests. Use kdrift_discover to find "
        "leaf overlays in a repo. Use kdrift_affected to see which overlays are "
        "impacted by specific file changes."
    ),
)


@server.tool(
    name="kdrift_diff",
    description=(
        "Diff kustomize overlays against a baseline git ref. Returns per-overlay, "
        "per-resource changes with structured diffs. Use after editing kustomize files "
        "to verify impact before committing."
    ),
)
def kdrift_diff(
    repo_path: str,
    ref: str = "HEAD",
    paths: list[str] | None = None,
    overlay: str | None = None,
    target_ref: str | None = None,
) -> str:
    """Run the full diff pipeline and return structured JSON results.

    When target_ref is provided, compares ref (baseline) vs target_ref
    instead of ref vs working tree.
    """
    repo_root = git.find_repo_root(Path(repo_path))
    proj_config = config.resolve_project_config(config.load_project_config(repo_root))

    path_list = [Path(p) for p in paths] if paths else None
    overlay_filter = Path(overlay) if overlay else None

    result = pipeline.run_diff(
        repo_root=repo_root,
        ref=ref,
        paths=path_list,
        overlay_filter=overlay_filter,
        kustomize_args=proj_config.kustomize_args,
        target_ref=target_ref,
        kustomize_env=proj_config.env or None,
    )

    return result.model_dump_json(indent=2)


@server.tool(
    name="kdrift_discover",
    description=(
        "Discover leaf kustomize overlays in a repository. By default, returns only "
        "overlays affected by current uncommitted changes (via git status). Pass "
        "show_all=true to list every leaf overlay in the repo."
    ),
)
def kdrift_discover(repo_path: str, show_all: bool = False) -> str:
    """List leaf overlays, scoped to current git changes by default."""
    repo_root = git.find_repo_root(Path(repo_path))

    graph = discover.DependencyGraph(repo_root)
    graph.build()

    if show_all:
        overlays = graph.leaf_overlays
        scope = "all"
    else:
        try:
            changed = git.changed_files("HEAD", repo_root=repo_root)
        except git.GitError:
            changed = []

        if not changed:
            return json.dumps(
                {"repo": str(repo_root), "leaf_overlays": [], "total": 0, "scope": "changed", "changed_files": 0},
                indent=2,
            )

        overlays = graph.affected_overlays(changed)
        scope = "changed"

    output = {
        "repo": str(repo_root),
        "leaf_overlays": [{"path": str(o.path), "kustomization_file": str(o.kustomization_file)} for o in overlays],
        "total": len(overlays),
        "scope": scope,
    }
    if scope == "changed":
        output["changed_files"] = len(changed)

    return json.dumps(output, indent=2)


@server.tool(
    name="kdrift_affected",
    description=(
        "Given a list of changed files, find which leaf overlays are affected. "
        "Use this to understand the blast radius of a change before running a full diff."
    ),
)
def kdrift_affected(repo_path: str, changed_files: list[str]) -> str:
    """Find overlays affected by the given changed files."""
    repo_root = git.find_repo_root(Path(repo_path))

    graph = discover.DependencyGraph(repo_root)
    graph.build()

    affected = graph.affected_overlays([Path(f) for f in changed_files])
    output = {
        "changed_files": changed_files,
        "affected_overlays": [str(o.path) for o in affected],
        "total": len(affected),
    }

    return json.dumps(output, indent=2)


@server.tool(
    name="kdrift_render",
    description=(
        "Render a specific kustomize overlay. Returns the full rendered YAML manifest. "
        "Useful for inspecting what a single overlay produces."
    ),
)
def kdrift_render(repo_path: str, overlay_path: str) -> str:
    """Render a single overlay and return the YAML output."""
    repo_root = git.find_repo_root(Path(repo_path))
    proj_config = config.resolve_project_config(config.load_project_config(repo_root))
    overlay = Path(overlay_path)

    result = render.render_overlay(
        overlay,
        repo_root / overlay,
        proj_config.kustomize_args,
        env=proj_config.env or None,
    )

    if not result.success:
        return json.dumps(
            {"error": result.error, "exit_code": result.exit_code, "overlay": str(overlay)},
            indent=2,
        )

    return result.output


def run_mcp_server() -> None:
    """Start the MCP server on stdio."""
    server.run(transport="stdio")
