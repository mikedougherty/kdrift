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
- **Config:** `pydantic-settings` BaseSettings in `config.py`, loaded from env vars with `APP_` prefix
- **Logging:** structlog with JSON output (production) / console (development)
- **Testing:** pytest with `unit`/`integration` markers, 80% coverage minimum

## Conventions

- All tool config in `pyproject.toml` (ruff, mypy, pytest, coverage)
- mypy strict mode -- avoid `type: ignore` unless no alternative exists
- Use `pydantic.SecretStr` for credential fields in config
- Pre-commit hooks enforce formatting and type checking on every commit


## CLI

- click-based CLI registered as `kdrift` entrypoint
- Run via `uv run kdrift` or `make run`


@./AGENTS.md
