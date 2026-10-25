.PHONY: help setup lint typecheck test run accounts providers clean

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## Install dependencies
	uv sync --all-extras

lint:  ## Lint and format check
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:  ## Static type check
	uv run mypy src/ --strict

test:  ## Run the test suite
	uv run pytest

run: setup  ## Run the wrapper: make run CMD="envelope list"
	uv run mycord-email $(CMD)

accounts: setup  ## List configured accounts
	uv run mycord-email accounts list

providers: setup  ## List provider templates
	uv run mycord-email providers

clean:  ## Remove caches
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
