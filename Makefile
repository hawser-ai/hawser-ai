.DEFAULT_GOAL := help
SHELL := /bin/bash

PY_DIRS := python
SCHEMA_DIR := api/schemas

.PHONY: help setup lint format typecheck test test-live schemas schemas-check openapi openapi-check check clean up down logs

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

openapi: ## Regenerate api/openapi/openapi.json from the hawserd app
	uv run hawserd openapi --out api/openapi/openapi.json

openapi-check: ## Fail if api/openapi/openapi.json is stale
	uv run hawserd openapi --out api/openapi/openapi.json --check

check: lint typecheck schemas-check openapi-check test ## Everything CI runs

COMPOSE := docker compose -f deploy/compose/docker-compose.yaml

up: ## Build and start Postgres, RustFS (S3), hawserd and a worker (docker compose)
	$(COMPOSE) up -d --build
	@echo "hawserd: http://localhost:8080  (API key: $${HAWSER_DEV_API_KEY:-hk_live_compose-dev-key-change-me-0000000000})"
	@echo "next:    hawser init --server http://localhost:8080 && export HAWSER_API_KEY=<key>"

down: ## Stop the compose stack (keeps volumes)
	$(COMPOSE) down

logs: ## Follow hawserd and worker logs
	$(COMPOSE) logs -f hawserd worker

clean: ## Remove caches and build output
	rm -rf .venv .pytest_cache .ruff_cache .hypothesis dist build
	find $(PY_DIRS) -name __pycache__ -type d -prune -exec rm -rf {} +
