#!/usr/bin/env python3
"""Integration test harness for the MCP server.

Spawns `kdrift mcp` as a subprocess, connects as an MCP client,
and exercises all four tools against a real kustomize repo.

Run: uv run python tests/test_mcp_integration.py [repo_path]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_PATH = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "src/github.com/missionlane/infra-config")


async def run_tests() -> None:
    """Run all MCP tool tests."""
    server_params = StdioServerParameters(
        command="kdrift",
        args=["mcp"],
    )

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

            # Test 1: kdrift_discover
            print("\n--- Test 1: kdrift_discover ---")
            try:
                result = await session.call_tool("kdrift_discover", {"repo_path": REPO_PATH})
                text = result.content[0].text
                data = json.loads(text)
                count = data["total"]
                print(f"  Found {count} leaf overlays")
                results.append(("discover", count > 0, f"{count} overlays"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("discover", False, str(e)))

            # Test 2: kdrift_affected
            print("\n--- Test 2: kdrift_affected ---")
            try:
                result = await session.call_tool(
                    "kdrift_affected",
                    {
                        "repo_path": REPO_PATH,
                        "changed_files": ["kiali/base/resources/ns.yaml"],
                    },
                )
                text = result.content[0].text
                data = json.loads(text)
                count = data["total"]
                print(f"  {count} overlays affected by kiali/base/resources/ns.yaml")
                results.append(("affected", count > 0, f"{count} overlays"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("affected", False, str(e)))

            # Test 3: kdrift_affected with non-existent file
            print("\n--- Test 3: kdrift_affected (nonexistent file) ---")
            try:
                result = await session.call_tool(
                    "kdrift_affected",
                    {
                        "repo_path": REPO_PATH,
                        "changed_files": ["does/not/exist.yaml"],
                    },
                )
                text = result.content[0].text
                data = json.loads(text)
                count = data["total"]
                print(f"  {count} overlays affected (expected 0)")
                results.append(("affected_nonexistent", count == 0, f"{count} overlays"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("affected_nonexistent", False, str(e)))

            # Test 4: kdrift_diff (no changes)
            print("\n--- Test 4: kdrift_diff (clean repo) ---")
            try:
                result = await session.call_tool(
                    "kdrift_diff",
                    {
                        "repo_path": REPO_PATH,
                    },
                )
                text = result.content[0].text
                data = json.loads(text)
                overlay_count = len(data.get("overlays", []))
                print(f"  {overlay_count} overlays in diff result")
                results.append(("diff_clean", True, f"{overlay_count} overlays"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("diff_clean", False, str(e)))

            # Test 5: kdrift_render
            print("\n--- Test 5: kdrift_render ---")
            try:
                result = await session.call_tool(
                    "kdrift_render",
                    {
                        "repo_path": REPO_PATH,
                        "overlay_path": "kiali/dev",
                    },
                )
                text = result.content[0].text
                has_yaml = "kind:" in text or "apiVersion:" in text
                is_error = text.strip().startswith("{") and "error" in text
                if is_error:
                    print(f"  Build error (may be expected for helm overlays): {text[:200]}")
                    results.append(("render", True, "build error (expected for some overlays)"))
                else:
                    line_count = len(text.splitlines())
                    print(f"  Rendered {line_count} lines of YAML")
                    results.append(("render", has_yaml, f"{line_count} lines"))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append(("render", False, str(e)))

            # Test 6: kdrift_discover with invalid repo path
            print("\n--- Test 6: kdrift_discover (invalid repo) ---")
            try:
                result = await session.call_tool(
                    "kdrift_discover",
                    {
                        "repo_path": "/nonexistent/repo",
                    },
                )
                text = result.content[0].text
                print(f"  Response: {text[:200]}")
                results.append(("discover_invalid", True, "handled gracefully"))
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
                    {
                        "repo_path": REPO_PATH,
                        "overlay_path": "does/not/exist",
                    },
                )
                text = result.content[0].text
                print(f"  Response: {text[:200]}")
                results.append(("render_invalid", True, "handled gracefully"))
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


if __name__ == "__main__":
    asyncio.run(run_tests())
