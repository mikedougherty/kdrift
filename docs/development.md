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

## Releasing

Releases are automated with [release-please](https://github.com/googleapis/release-please); version numbers are derived from [Conventional Commits](https://www.conventionalcommits.org/). **Do not hand-edit the version in `pyproject.toml`** — release-please owns it.

- **Cut a release:** merge commits with releasable types (`feat:`, `fix:`) to `main`. release-please opens/updates a "Release PR" with the version bump + CHANGELOG; merging that Release PR tags the version and publishes to PyPI.
- **Release candidate (pre-release):** publish an RC to PyPI to test before the stable cut, via the manual `publish-rc.yml` workflow:
  ```bash
  gh workflow run publish-rc.yml -f version=<VERSION>rc<N>   # e.g. 0.1.6rc1
  ```
  RCs are not installed by default; test one with `uv tool install kdrift==<VERSION>rc<N>`.

Full procedure — the conventional-commit → version-bump mapping, how to pick the next RC number, and verification steps — is in [`AGENTS.md`](../AGENTS.md) under "Releases (release-please)".
