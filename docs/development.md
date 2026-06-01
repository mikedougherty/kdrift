# Development Guide

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
git clone <repo-url>
cd kdrift
make deps
cp .env.example .env  # Fill in required values
```

## Common Tasks

```bash
make help          # Show all available targets
make validate      # Run all checks (CI equivalent)
make test          # Run tests with coverage
make lint          # Run linter
make format        # Format code
make typecheck     # Run mypy
```

## Pre-commit Hooks

Install hooks on first setup:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

## Testing

Tests use pytest with markers:

```bash
make test              # All tests
make test-unit         # Unit tests only
make test-integration  # Integration tests only
uv run pytest tests/test_config.py -v  # Single file
```

Coverage threshold is 80% (enforced in CI).
