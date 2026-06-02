# kdrift

Kustomize manifest drift detection tool

## Quick Reference

```bash
make deps        # Install dependencies
make validate    # Run all checks (lint + typecheck + test)
make test        # Run tests
make typecheck   # Run mypy
```

## Architecture

- **Package layout:** `src/kdrift/` (src layout)
- **Config:** `pydantic-settings` BaseSettings in `config.py`, env vars with `KDRIFT_` prefix
- **Project config:** `.kdrift.yaml` with upward directory walking (project > org > user XDG)
- **Logging:** structlog with JSON output (production) / console (development)
- **Testing:** pytest with `unit`/`integration` markers, 80% coverage minimum

### Module Map

| Module | Purpose |
|--------|---------|
| `cli.py` | click entry point, --watch/--check/--format flags |
| `discover.py` | Parse kustomization.yaml files, build reverse dependency DAG, find affected overlays |
| `render.py` | Run kustomize build as subprocess, baseline caching, parallel rendering |
| `diff.py` | Two-phase resource matching (exact GVK+ns+name, then generator-aware), unified diff |
| `pipeline.py` | Shared discover->render->diff orchestration for all frontends |
| `git.py` | Ref resolution, changed files, temporary worktree management |
| `models.py` | Pydantic models (ResourceId, OverlayResult, DiffResult, RenderResult) |
| `config.py` | AppConfig (env vars) + ProjectConfig (.kdrift.yaml hierarchy) |
| `watch.py` | Filesystem monitoring with debouncing via watchfiles |
| `logging.py` | structlog configuration |

### Key Design Decisions

- **Kustomize as subprocess, not library:** decouples from kustomize version, user controls the binary
- **Git-status-driven discovery:** `git diff --name-only` determines changed files, dependency graph maps to overlays
- **Two-phase resource matching:** Phase 1 exact GVK+ns+name, Phase 2 generator-aware with kustomize hash charset (`bcdfghjklmnpqrstvwxz2456789`)
- **Baseline caching:** keyed by ref+overlay+kustomize-args+version, stored in `~/.cache/kdrift/`
- **Read-only git operations only:** worktrees for baseline rendering (separate index, no locks)

## Conventions

- All tool config in `pyproject.toml` (ruff, mypy, pytest, coverage)
- mypy strict mode -- avoid `type: ignore` unless no alternative exists
- Use `pydantic.SecretStr` for credential fields in config
- Pre-commit hooks enforce formatting and type checking on every commit

## CLI

- click-based CLI registered as `kdrift` entrypoint
- Run via `uv run kdrift` or `make run`
- Supports `-C`/`--repo` flag to target external repositories

@./AGENTS.md
