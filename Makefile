.PHONY: deps run lint format format-check typecheck test test-unit test-integration validate clean help

deps: ## Install/sync all dependencies
	@uv sync --all-groups

run: ## Run the CLI
	@uv run kdrift

lint: ## Run linter
	@uv run ruff check .

format: ## Format code and fix auto-fixable lint issues
	@uv run ruff format .
	@uv run ruff check --fix .

format-check: ## Check formatting without modifying files
	@uv run ruff format --check .

typecheck: ## Run type checker
	@uv run mypy src/kdrift

test: ## Run all tests
	@uv run pytest

test-unit: ## Run unit tests only
	@uv run pytest -m unit

test-integration: ## Run integration tests only
	@uv run pytest -m integration

validate: ## Run all checks (CI equivalent)
	@$(MAKE) lint
	@$(MAKE) format-check
	@$(MAKE) typecheck
	@$(MAKE) test

clean: ## Remove build artifacts
	@rm -rf .mypy_cache .pytest_cache .ruff_cache htmlcov coverage.xml test-results/ .coverage

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
