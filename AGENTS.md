# Agent Instructions

## Verification Commands

```bash
make validate    # Full CI-equivalent check
make test        # Tests with coverage
make typecheck   # mypy strict mode
make lint        # ruff linter
```

## CI/CD

- CI runs on push to main and PRs: lint -> format-check -> typecheck -> test -> SonarQube -> container-build
- Container images built and published via shared workflow on merge to main
- Self-hosted GitHub Actions runners

## Key Files

| File | Purpose |
|------|---------|
| `src/kdrift/config.py` | Application config (pydantic-settings) |
| `src/kdrift/logging.py` | structlog configuration |
| `tests/conftest.py` | Shared test fixtures |
| `Makefile` | Standard development targets |
| `pyproject.toml` | All tool configuration |
| `sonar-project.properties` | SonarQube project settings |
