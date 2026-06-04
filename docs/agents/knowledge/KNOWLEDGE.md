# kdrift Knowledge

Kustomize manifest drift detection. Shows exactly what your kustomize edits will change in rendered Kubernetes manifests before you commit, push, or apply.

## Core Concepts

**Leaf overlay**: A kustomization directory that no other kustomization references. These are deployment targets (e.g., `k8s/dev`, `k8s/prod`). Base directories that get referenced by overlays are not leaves.

**Dependency graph**: kdrift builds a reverse dependency map from every file to the leaf overlays that transitively include it. When you edit `k8s/base/deployment.yaml`, kdrift knows which overlays (`k8s/dev`, `k8s/staging`, `k8s/prod`) are affected.

**Baseline vs candidate**: The baseline is the rendered output at a git ref (default: HEAD). The candidate is the rendered output from the working tree (or a second ref). kdrift diffs them per-resource.

**Two-phase resource matching**: Phase 1 matches resources by exact GVK + namespace + name. Phase 2 handles configMapGenerator/secretGenerator hash-suffixed names using the kustomize hash charset (`bcdfghjklmnpqrstvwxz2456789`).

**Two-ref comparison**: Compare any two git refs (`--ref main~5..main~2`) instead of working tree vs HEAD. Uses two temporary worktrees.

## Delivery Surfaces

| Surface | Invocation | Best for |
|---------|-----------|----------|
| **CLI** | `kdrift diff` | Terminal workflows, CI/CD, pre-commit hooks |
| **MCP** | `kdrift mcp` (stdio) | AI agents (Claude Code, etc.) with structured JSON |
| **LSP** | `kdrift lsp` (stdio) | IDE integration (VS Code, Neovim) with diagnostics on save |

## Configuration

### Project config (`.kdrift.yaml`)

kdrift searches upward from CWD for `.kdrift.yaml`:

```yaml
kustomize_args:
  - "--enable-helm"
  - "--load-restrictor"
  - "LoadRestrictionsNone"
kustomize_binary: /usr/local/bin/kustomize
env:
  HELM_REGISTRY_TOKEN: "abc123"
  SOPS_AGE_KEY_FILE: "/path/to/key"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `kustomize_args` | `list[str]` | `["--enable-helm", "--load-restrictor", "LoadRestrictionsNone"]` | Args passed to `kustomize build` |
| `kustomize_binary` | `str` | `None` (uses PATH) | Path to kustomize binary |
| `env` | `dict[str, str]` | `{}` | Extra env vars injected into the kustomize subprocess |

Search order: project directory, parent directories, `$XDG_CONFIG_HOME/kdrift/`.

### Environment variables

All project config fields can be overridden via environment variables. Env vars take precedence over `.kdrift.yaml` values.

| Variable | Default | Description |
|----------|---------|-------------|
| `KDRIFT_LOG_LEVEL` | `WARNING` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `KDRIFT_LOG_FORMAT` | `json` | Log format (`json` or `console`) |
| `KDRIFT_KUSTOMIZE_BINARY` | None | Override `kustomize_binary` from yaml |
| `KDRIFT_KUSTOMIZE_ARGS` | None | Override `kustomize_args` from yaml (space-separated, supports quoting) |
| `KDRIFT_KUSTOMIZE_ENV_<NAME>` | None | Inject `<NAME>=<value>` into the kustomize subprocess env |

### MCP configuration

Add to your agent's MCP config (e.g., `.claude.json`, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "kdrift": {
      "command": "kdrift",
      "args": ["mcp"]
    }
  }
}
```

### LSP configuration (VS Code)

Using the Generic LSP Client (glspc) extension:

```json
{
  "glspc.serverCommand": "kdrift",
  "glspc.serverCommandArguments": ["lsp"]
}
```

Add `--debug` to enable file logging to `~/.cache/kdrift/kdrift.log`.

## MCP Tools Reference

| Tool | Description |
|------|-------------|
| `kdrift_diff` | Diff overlays against a baseline ref. Returns per-overlay, per-resource structured JSON. Supports `target_ref` for two-ref comparison. |
| `kdrift_discover` | Find leaf overlays. Defaults to git-changed overlays; `show_all=true` for the full list. |
| `kdrift_affected` | Given a list of changed files, find which overlays are affected. |
| `kdrift_render` | Render a single overlay to YAML. |

## Output Structure

All tools return JSON. The diff result structure:

```json
{
  "ref": "abc1234",
  "target_ref": null,
  "overlays": [
    {
      "path": "k8s/dev",
      "changes": [
        {
          "resource_id": {
            "group": "apps", "version": "v1", "kind": "Deployment",
            "namespace": "myapp", "name": "api-server"
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

Status values: `modified`, `added`, `removed`.

## Error Handling

When `kustomize build` fails for one overlay, kdrift reports the error and continues with others. The `error` field is set per-overlay. Exit code is non-zero if any overlay errored.

Baseline build failures (the ref version was already broken) are reported as `"baseline build failed (pre-existing)"` to distinguish from regressions you introduced.

## Caching

Baseline renders are cached at `~/.cache/kdrift/` keyed by ref SHA + overlay path + kustomize version + args. Working tree renders are never cached.

## Prerequisites

- `kustomize` binary on PATH (kdrift shells out to it, does not embed kustomize)
- Git repository with at least one commit
- Python 3.13+ (for installation via `uv tool install`)
