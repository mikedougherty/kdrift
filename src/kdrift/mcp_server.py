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
) -> str:
    """Run the full diff pipeline and return structured JSON results."""
    repo_root = git.find_repo_root(Path(repo_path))
    proj_config = config.load_project_config(repo_root)

    path_list = [Path(p) for p in paths] if paths else None
    overlay_filter = Path(overlay) if overlay else None

    result = pipeline.run_diff(
        repo_root=repo_root,
        ref=ref,
        paths=path_list,
        overlay_filter=overlay_filter,
        kustomize_args=proj_config.kustomize_args,
    )

    return result.model_dump_json(indent=2)


@server.tool(
    name="kdrift_discover",
    description=(
        "Discover all leaf kustomize overlays in a repository. Leaf overlays are "
        "deployment targets (not referenced by other overlays as bases/components)."
    ),
)
def kdrift_discover(repo_path: str) -> str:
    """List all leaf overlays in the repository."""
    repo_root = git.find_repo_root(Path(repo_path))

    graph = discover.DependencyGraph(repo_root)
    graph.build()

    leaves = graph.leaf_overlays
    output = {
        "repo": str(repo_root),
        "leaf_overlays": [{"path": str(o.path), "kustomization_file": str(o.kustomization_file)} for o in leaves],
        "total": len(leaves),
    }

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
    proj_config = config.load_project_config(repo_root)
    overlay = Path(overlay_path)

    result = render.render_overlay(
        overlay,
        repo_root / overlay,
        proj_config.kustomize_args,
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
