# kdrift

Kustomize manifest drift detection. Discovers which overlays are affected by your changes, renders baselines and candidates, and diffs per-resource.

## Install

```bash
uv tool install kdrift    # or: uvx kdrift
```

## Usage

```bash
kdrift                              # diff all affected overlays vs HEAD
kdrift k8s/base/deployment.yaml     # diff overlays affected by this file
kdrift --overlay k8s/dev            # diff only this overlay
kdrift --ref main~3                 # diff against a specific ref
kdrift -C /path/to/repo             # target a different repository
kdrift --format json                # structured JSON output
kdrift --check                      # exit non-zero if drift exists (CI/pre-commit)
kdrift --watch                      # continuous mode: re-diff on file save
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

## Documentation

- [Development Guide](docs/development.md)
- [Configuration](docs/configuration.md)
- [Agent SKILL](docs/SKILL.md)
