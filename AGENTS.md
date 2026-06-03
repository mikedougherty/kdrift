# Agent Instructions

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
