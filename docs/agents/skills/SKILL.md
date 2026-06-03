# kdrift Skill

Detect kustomize manifest drift before committing or applying changes. Works via MCP tools (structured JSON, preferred for agents) or CLI (terminal/CI).

## When to Use

- After editing kustomize overlays, bases, patches, helm values, or generator files
- Before committing changes to verify rendered manifest impact
- When reviewing kustomize PRs to understand the blast radius
- As a CI gate (`--check` mode exits non-zero on drift)
- To compare what changed between two commits (`--ref A..B`)

## Quick Start

### MCP (preferred for agents)

If kdrift is configured as an MCP server, use the tools directly:

```
kdrift_discover(repo_path="/path/to/repo")
kdrift_diff(repo_path="/path/to/repo")
```

### CLI

```bash
# Install
uv tool install kdrift

# Diff all affected overlays against HEAD
kdrift diff

# Target a specific repo
kdrift diff -C /path/to/repo
```

## Workflow

### Step 1: Discover what's affected

Before diffing, check which overlays your changes touch:

**MCP:**
```
kdrift_discover(repo_path=".")           # overlays affected by uncommitted changes
kdrift_discover(repo_path=".", show_all=true)  # all leaf overlays in the repo
```

**CLI:**
```bash
kdrift diff --format json   # discovery is implicit in the diff pipeline
```

### Step 2: Diff to see the impact

**MCP:**
```
kdrift_diff(repo_path=".")                           # working tree vs HEAD
kdrift_diff(repo_path=".", ref="main")               # working tree vs main
kdrift_diff(repo_path=".", ref="main~5", target_ref="main")  # compare two refs
kdrift_diff(repo_path=".", overlay="k8s/dev")        # single overlay only
```

**CLI:**
```bash
kdrift diff                          # working tree vs HEAD, unified diff
kdrift diff --format json            # structured JSON output
kdrift diff --ref main~5..main       # compare two commits
kdrift diff --overlay k8s/dev        # single overlay only
kdrift diff -C /path/to/other/repo   # target a different repo
```

### Step 3: Interpret results

Each overlay in the result contains:
- **changes**: list of resources that differ, with status (`modified`, `added`, `removed`) and unified diff text
- **error**: set if `kustomize build` failed for this overlay (null on success)

No changes = your edit doesn't affect rendered manifests (e.g., a comment change, or a file not referenced by any overlay).

### Step 4: Check for specific files (optional)

If you want to know which overlays a specific file affects without running a full diff:

**MCP:**
```
kdrift_affected(repo_path=".", changed_files=["k8s/base/deployment.yaml"])
```

### Step 5: Render a single overlay (optional)

Inspect the full rendered YAML for one overlay:

**MCP:**
```
kdrift_render(repo_path=".", overlay_path="k8s/dev")
```

## CI / Pre-commit Usage

```bash
kdrift diff --check   # exits non-zero if any overlay has drift
```

Use in CI pipelines or as a pre-commit hook to prevent unreviewed manifest changes.

## Watch Mode (CLI only)

```bash
kdrift diff --watch              # re-diff on every file save
kdrift diff --watch --format json  # streaming JSON output
```

## Common Patterns

### Verifying a kustomize change is safe

After editing a patch or helm values file:
1. Run `kdrift_diff` (or `kdrift diff --format json`)
2. Check that only the intended resources changed
3. Verify no overlays errored (broken kustomize builds)
4. If unexpected overlays are affected, check the dependency graph with `kdrift_discover`

### Comparing what a PR changed

```bash
# What did the last 3 commits change in rendered manifests?
kdrift diff --ref HEAD~3..HEAD
```

**MCP:**
```
kdrift_diff(repo_path=".", ref="HEAD~3", target_ref="HEAD")
```

### Scoping to specific paths

Both CLI and MCP accept path arguments to narrow changed-file detection:

```bash
kdrift diff k8s/base/   # only changes under k8s/base/
```

**MCP:**
```
kdrift_diff(repo_path=".", paths=["k8s/base/"])
```
