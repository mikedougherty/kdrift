# kdrift Agent Skill

Use kdrift to detect kustomize manifest drift before pushing. Replaces manual `kustomize build` + diff workflows.

## When to Use

- Before committing changes to kustomize overlays, bases, patches, helm values, or configMapGenerator files
- When reviewing kustomize PRs to verify rendered manifest impact
- As a pre-commit or CI gate (--check mode)
- During watch mode for continuous feedback while editing

## Installation

```bash
uv tool install kdrift   # system-wide
uvx kdrift               # one-shot, no install
```

## Commands

### One-shot diff (most common)

```bash
# Diff all affected overlays against HEAD
kdrift

# Diff overlays affected by specific files
kdrift k8s/base/deployment.yaml

# Diff only a specific overlay
kdrift --overlay k8s/dev

# Diff against a specific ref
kdrift --ref main~3

# Target a different repository
kdrift -C /path/to/repo
kdrift /path/to/repo/k8s/
```

### Structured output for agents

```bash
kdrift --format json
```

Returns JSON with per-overlay, per-resource granularity:
```json
{
  "ref": "abc1234",
  "overlays": [
    {
      "path": "k8s/dev",
      "changes": [
        {
          "resource_id": {
            "group": "apps",
            "version": "v1",
            "kind": "Deployment",
            "namespace": "myapp",
            "name": "api-server"
          },
          "status": "modified",
          "diff_text": "...",
          "lines_added": 1,
          "lines_removed": 1
        }
      ],
      "error": null
    }
  ]
}
```

### CI / pre-commit gate

```bash
kdrift --check  # exits non-zero if any overlay has drift
```

### Watch mode

```bash
kdrift --watch             # continuous diff on file save
kdrift --watch --format json  # streaming JSON lines
```

## Configuration

kdrift searches for `.kdrift.yaml` upward from CWD (project > org > user XDG):

```yaml
kustomize_args:
  - "--enable-helm"
  - "--load-restrictor"
  - "LoadRestrictionsNone"
```

## How It Works

1. `git diff --name-only HEAD` finds changed files
2. Dependency graph maps changed files to affected leaf overlays
3. `kustomize build` renders both baseline (via git worktree) and candidate
4. Per-resource structured diff with two-phase matching (exact GVK+ns+name, then generator-aware)
5. Baseline renders are cached; working tree renders are always fresh

## Error Handling

When `kustomize build` fails on one overlay, kdrift reports the error and continues with others. JSON output includes an `error` field per overlay. Exit code is non-zero if any overlay errored.

## Agent Integration Patterns

When working on kustomize files, run `kdrift --format json` after edits to verify impact:

```python
import json
import subprocess

result = subprocess.run(
    ["kdrift", "--format", "json", "-C", repo_path],
    capture_output=True, text=True,
)
if result.returncode != 0:
    # Parse errors from JSON
    data = json.loads(result.stdout)
    for overlay in data["overlays"]:
        if overlay["error"]:
            print(f"Build error in {overlay['path']}: {overlay['error']}")
```
