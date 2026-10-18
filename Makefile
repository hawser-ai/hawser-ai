.DEFAULT_GOAL := help
SHELL := /bin/bash

PY_DIRS := python
SCHEMA_DIR := api/schemas

.PHONY: help setup lint format typecheck test test-live schemas schemas-check check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv, install all packages and pre-commit hooks
	uv sync --all-packages
	uv run pre-commit install

lint: ## Ruff lint + format check
	uv run ruff check $(PY_DIRS)
	uv run ruff format --check $(PY_DIRS)

format: ## Ruff format and auto-fix
	uv run ruff format $(PY_DIRS)
	uv run ruff check --fix $(PY_DIRS)

typecheck: ## Pyright (strict)
	uv run pyright

test: ## Unit, contract and recorded tests (no network, no provider keys)
	HAWSER_RECORD_MODE=replay uv run pytest -n auto

test-live: ## Live-model tests; requires provider keys and HAWSER_LIVE_BUDGET_USD
	@test -n "$$HAWSER_LIVE_BUDGET_USD" || (echo "set HAWSER_LIVE_BUDGET_USD" && exit 1)
	HAWSER_RECORD_MODE=live uv run pytest -m live

schemas: ## Regenerate api/schemas from the Pydantic models
	uv run python -m hawser.models.export --out $(SCHEMA_DIR)

schemas-check: ## Fail if api/schemas is stale
	uv run python -m hawser.models.export --out $(SCHEMA_DIR) --check

check: lint typecheck schemas-check test ## Everything CI runs

clean: ## Remove caches and build output
	rm -rf .venv .pytest_cache .ruff_cache .hypothesis dist build
	find $(PY_DIRS) -name __pycache__ -type d -prune -exec rm -rf {} +
