#!/usr/bin/env python3
"""Integration test harness for the MCP server.

Spawns `kdrift mcp` as a subprocess, connects as an MCP client, and exercises
all four tools against a self-contained kustomize repo built in a temp dir.

The fixture is a shared base consumed by three leaf overlays (dev/staging/prod)
with one uncommitted edit to the base, so discover/affected/diff all see real
transitive drift. No external repo is required. Pass a repo path to probe a real
repo instead (assertions are tuned to the fixture and may not hold).

Run: uv run --group test python tests/test_mcp_integration.py [repo_path]

Requires `kdrift`, `kustomize`, and `git` on PATH.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_NAMESPACE = "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: demo\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def build_fixture_repo(root: Path) -> Path:
    """Build a git repo: a shared base + dev/staging/prod overlays, then drift.

    All three overlays consume ``k8s/base``; the base is edited (uncommitted)
    so a diff against HEAD reports transitive drift in every overlay.
    """
    base = root / "k8s" / "base"
    base.mkdir(parents=True)
    (base / "kustomization.yaml").write_text("resources:\n  - namespace.yaml\n")
    (base / "namespace.yaml").write_text(_NAMESPACE)

    for env in ("dev", "staging", "prod"):
        d = root / "k8s" / env
        d.mkdir()
        (d / "kustomization.yaml").write_text(f"resources:\n  - ../base\nnamePrefix: {env}-\n")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@kdrift.local")
    _git(root, "config", "user.name", "kdrift test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture: base + overlays")

    # Uncommitted drift in the shared base -> transitively affects all overlays.
    (base / "namespace.yaml").write_text(_NAMESPACE + '  labels:\n    drift: "yes"\n')

    return root


def _preflight() -> None:
    missing = [b for b in ("kdrift", "kustomize", "git") if shutil.which(b) is None]
    if missing:
        print(f"MISSING BINARIES: {', '.join(missing)} -- cannot run integration test")
        sys.exit(2)


async def run_tests(repo_path: str) -> None:
    """Run all MCP tool tests against repo_path."""
    server_params = StdioServerParameters(command="kdrift", args=["mcp"])

    results: list[tuple[str, bool, str]] = []

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Test 0: List tools
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"Available tools: {tool_names}")
            results.append(
                (
                    "list_tools",
                    len(tool_names) == 4,
                    f"expected 4 tools, got {len(tool_names)}: {tool_names}",
                )
            )

            # Test 1: kdrift_discover (scoped to uncommitted changes)
            print("\n--- Test 1: kdrift_discover ---")
            try:
                result = await session.call_tool("kdrift_discover", {"repo_path": repo_path})
                data = json.loads(result.content[0].text)
                count = data["total"]
                print(f"  Found {count} leaf overlays")
                results.append(("discover", count == 3, f"{count} overlays (expected 3)"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("discover", False, str(e)))

            # Test 2: kdrift_affected (base change fans out to all overlays)
            print("\n--- Test 2: kdrift_affected ---")
            try:
                result = await session.call_tool(
                    "kdrift_affected",
                    {"repo_path": repo_path, "changed_files": ["k8s/base/namespace.yaml"]},
                )
                data = json.loads(result.content[0].text)
                count = data["total"]
                print(f"  {count} overlays affected by k8s/base/namespace.yaml")
                results.append(("affected", count == 3, f"{count} overlays (expected 3)"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("affected", False, str(e)))

            # Test 3: kdrift_affected with non-existent file
            print("\n--- Test 3: kdrift_affected (nonexistent file) ---")
            try:
                result = await session.call_tool(
                    "kdrift_affected",
                    {"repo_path": repo_path, "changed_files": ["does/not/exist.yaml"]},
                )
                data = json.loads(result.content[0].text)
                count = data["total"]
                print(f"  {count} overlays affected (expected 0)")
                results.append(("affected_nonexistent", count == 0, f"{count} overlays"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("affected_nonexistent", False, str(e)))

            # Test 4: kdrift_diff (base drift shows up in every overlay)
            print("\n--- Test 4: kdrift_diff ---")
            try:
                result = await session.call_tool("kdrift_diff", {"repo_path": repo_path})
                data = json.loads(result.content[0].text)
                overlays = data.get("overlays", [])
                # has_changes is a non-serialized property; a non-empty changes list is drift.
                drifted = [o for o in overlays if o.get("changes")]
                print(f"  {len(overlays)} overlays in diff, {len(drifted)} with drift")
                results.append(("diff", len(drifted) == 3, f"{len(drifted)} drifted overlays (expected 3)"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("diff", False, str(e)))

            # Test 5: kdrift_render (produces real YAML)
            print("\n--- Test 5: kdrift_render ---")
            try:
                result = await session.call_tool(
                    "kdrift_render",
                    {"repo_path": repo_path, "overlay_path": "k8s/dev"},
                )
                text = result.content[0].text
                has_yaml = "kind: Namespace" in text and "name: demo" in text
                line_count = len(text.splitlines())
                print(f"  Rendered {line_count} lines of YAML")
                results.append(("render", has_yaml, f"{line_count} lines, manifest present={has_yaml}"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("render", False, str(e)))

            # Test 6: kdrift_discover with invalid repo path
            print("\n--- Test 6: kdrift_discover (invalid repo) ---")
            try:
                result = await session.call_tool(
                    "kdrift_discover",
                    {"repo_path": "/nonexistent/repo"},
                )
                text = result.content[0].text
                print(f"  Response: {text[:200]}")
                results.append(("discover_invalid", "error" in text.lower(), text[:100]))
            except Exception as e:
                error_str = str(e)
                is_expected = "git" in error_str.lower() or "not found" in error_str.lower()
                print(f"  Error (expected): {error_str[:200]}")
                results.append(("discover_invalid", is_expected, error_str[:100]))

            # Test 7: kdrift_render with nonexistent overlay
            print("\n--- Test 7: kdrift_render (nonexistent overlay) ---")
            try:
                result = await session.call_tool(
                    "kdrift_render",
                    {"repo_path": repo_path, "overlay_path": "does/not/exist"},
                )
                text = result.content[0].text
                print(f"  Response: {text[:200]}")
                results.append(("render_invalid", "error" in text.lower(), "handled gracefully"))
            except Exception as e:
                print(f"  Error: {str(e)[:200]}")
                results.append(("render_invalid", True, "exception handled"))

    # Summary
    print("\n" + "=" * 50)
    print("RESULTS")
    print("=" * 50)
    all_pass = True
    for name, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  {status}: {name} -- {detail}")

    print()
    if all_pass:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)


def main() -> None:
    _preflight()
    if len(sys.argv) > 1:
        # Probe a real repo; fixture-tuned assertions may not hold.
        asyncio.run(run_tests(sys.argv[1]))
        return
    with tempfile.TemporaryDirectory(prefix="kdrift-mcp-it-") as tmp:
        repo = build_fixture_repo(Path(tmp))
        asyncio.run(run_tests(str(repo)))


if __name__ == "__main__":
    main()
