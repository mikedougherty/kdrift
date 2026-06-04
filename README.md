# kdrift

Kustomize manifest drift detection. Discovers which overlays are affected by your changes, renders baselines and candidates, and diffs per-resource.

## Install

```bash
# From GitHub (recommended for now)
uv tool install git+https://github.com/mikedougherty/kdrift

# From PyPI (once published)
uv tool install kdrift
```

Requires Python 3.13+ and `kustomize` on PATH.

## Usage

```bash
kdrift diff                             # diff all affected overlays vs HEAD
kdrift diff k8s/base/deployment.yaml    # diff overlays affected by this file
kdrift diff --overlay k8s/dev           # diff only this overlay
kdrift diff --ref main~3                # diff against a specific ref
kdrift diff --ref main~5..main~2        # compare two commits
kdrift diff -C /path/to/repo            # target a different repository
kdrift diff --format json               # structured JSON output
kdrift diff --check                     # exit non-zero if drift exists (CI/pre-commit)
kdrift diff --watch                     # continuous mode: re-diff on file save
```

## How It Works

1. `git diff --name-only HEAD` finds changed files
2. Dependency graph maps changes to affected leaf overlays (parses all `kustomization.yaml` reference types)
3. `kustomize build` renders baseline (via git worktree, cached) and candidate (working tree)
4. Two-phase per-resource diff: exact GVK+namespace+name match, then generator-aware matching for hash-suffixed ConfigMap/Secret names
5. Output as unified diff or structured JSON

## Configuration

Create `.kdrift.yaml` anywhere in your directory tree (searched upward from CWD):

```yaml
kustomize_args:
  - "--enable-helm"
  - "--load-restrictor"
  - "LoadRestrictionsNone"
```

## Development

```bash
make deps        # Install dependencies
make validate    # Run all checks (lint + typecheck + test)
make test        # Tests only
make typecheck   # mypy strict mode
```

See [docs/development.md](docs/development.md) for detailed setup.

## Quick Start: MCP Server for Claude Code

Give your AI agent kustomize drift detection in two steps:

**1. Install kdrift:**

```bash
uv tool install git+https://github.com/mikedougherty/kdrift
```

**2. Add to your Claude Code MCP config** (`.claude.json` or project `.mcp.json`):

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

That's it. Your agent now has four tools: `kdrift_diff`, `kdrift_discover`, `kdrift_affected`, `kdrift_render`. Ask it to "check what my kustomize changes affect" and it will use them.

**3. (Optional) Add agent instructions** for deeper context on how to use kdrift:

```markdown
@path/to/kdrift/docs/agents/AGENTS.md
```

Or copy `docs/agents/` into your project's agent instructions directory. The files are self-contained and agent-agnostic.

## Agent Integration

kdrift ships with agent-readable instructions in `docs/agents/`. These work with any AI coding assistant that supports `AGENTS.md` or similar instruction files.

### VS Code Extension

The `vscode-kdrift/` directory contains a VS Code extension that shows drift diffs in a side panel, modeled after the built-in Markdown Preview. See [vscode-kdrift/README.md](vscode-kdrift/README.md) for setup and development.

### LSP Server (IDE integration)

```bash
kdrift lsp          # stdio transport, configure in your LSP client
kdrift lsp --debug  # enable file logging to ~/.cache/kdrift/kdrift.log
```

Provides diagnostics on save, CodeLens annotations, and hover info.

## Documentation

- [Development Guide](docs/development.md)
- [Configuration](docs/configuration.md)
- [Agent Instructions](docs/agents/AGENTS.md)
