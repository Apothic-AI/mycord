.PHONY: help setup lint typecheck test test-live doctor clean

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
	uv run mypy src/

test:  ## Run the offline test suite
	uv run pytest -m "not live"

test-live:  ## Run every test; needs `mycord-ferdium launch --detach` first
	uv run pytest

doctor: setup  ## Report Ferdium install + configuration state
	uv run mycord-ferdium doctor

clean:  ## Remove caches
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
