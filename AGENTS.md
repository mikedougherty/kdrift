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

## Verification Commands

```bash
make validate    # Full CI-equivalent check
make test        # Tests with coverage
make typecheck   # mypy strict mode
make lint        # ruff linter
```

## CI/CD

- CI runs on push to main and PRs: lint -> format-check -> typecheck -> test
- ubuntu-latest runners (not self-hosted)

### Releases (release-please)

Releases are automated via [release-please](https://github.com/googleapis/release-please). The full pipeline:

```
conventional commits on main
  → release-please opens/updates a "Release PR" (version bump + CHANGELOG)
  → merge the Release PR
  → release-please creates git tag (v0.X.Y) + GitHub release
  → publish.yaml triggers on the release → builds and publishes to PyPI
```

**Version bumps are automated.** Do not manually edit `version` in `pyproject.toml`. Release-please owns that field and updates it in the Release PR based on conventional commit types.

#### Conventional commit → version bump mapping

| Prefix | Bump | Example |
|--------|------|---------|
| `feat:` | patch (minor when >= 1.0) | New CLI flag, new MCP tool |
| `fix:` | patch | Bug fix |
| `feat!:` or `BREAKING CHANGE:` | minor (major when >= 1.0) | Removed flag, changed output format |
| `docs:`, `chore:`, `refactor:`, `test:`, `ci:` | no release | Documentation, maintenance |

While the project is pre-1.0, `bump-minor-pre-major` and `bump-patch-for-minor-pre-major` are enabled, so breaking changes bump minor and features bump patch.

#### How to trigger a release

1. Push commits with releasable types (`feat:`, `fix:`) to main
2. Release-please automatically opens or updates a Release PR
3. Review and merge the Release PR when ready to cut a release
4. Tag, GitHub release, and PyPI publish happen automatically

#### Configuration files

| File | Purpose |
|------|---------|
| `release-please-config.json` | Release strategy, version bump rules, changelog path |
| `.release-please-manifest.json` | Tracks current version (updated by release-please) |
| `.github/workflows/release-please.yml` | Runs release-please on every push to main |
| `.github/workflows/publish.yml` | Builds and publishes to PyPI on GitHub release |

## Key Files

| File | Purpose |
|------|---------|
| `src/kdrift/config.py` | Application config (pydantic-settings) |
| `src/kdrift/logging.py` | structlog configuration |
| `src/kdrift/lsp_server.py` | LSP server (pygls) |
| `src/kdrift/mcp_server.py` | MCP server (FastMCP) |
| `tests/conftest.py` | Shared test fixtures |
| `tests/test_mcp_integration.py` | MCP integration test harness |
| `Makefile` | Standard development targets |
| `pyproject.toml` | All tool configuration |

## Agent Development Loop

When iterating on kdrift itself, use `--debug` to enable file logging and verbose output:

### LSP development

```bash
# Configure VS Code: glspc.serverCommand="kdrift", glspc.serverCommandArguments=["lsp", "--debug"]
# Or without debug: glspc.serverCommandArguments=["lsp"]

# Hot-reload after code changes (no VS Code restart needed):
pkill -USR1 -f "kdrift lsp"

# Full reinstall (needed if lsp_server.py itself changes):
uv tool install -e ~/src/github.com/mikedougherty/kdrift --force
pkill -f "kdrift lsp"  # VS Code auto-restarts

# Monitor logs:
tail -f ~/.cache/kdrift/kdrift.log
```

### MCP development

```bash
# Run integration tests (self-contained, no session restart):
uv run python tests/test_mcp_integration.py

# Or test against a specific repo:
uv run python tests/test_mcp_integration.py /path/to/kustomize-repo
```

### Debug flag behavior

`--debug` on `lsp` and `mcp` subcommands enables:
- DEBUG log level (all internal events logged)
- File logging to `~/.cache/kdrift/kdrift.log`
- Full-repo diff on SIGUSR1 reload (LSP only)

Without `--debug`, LSP/MCP run quietly with WARNING level, no file log.
