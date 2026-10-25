.PHONY: help setup lint typecheck test test-live run login session clean

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

test-live:  ## Run every test, including ones that hit the live account
	uv run pytest

run: setup  ## Run the CLI: make run CMD="threads --limit 5"
	uv run gvoice-skill $(CMD)

login: setup  ## Capture the cookie jar from the signed-in browser profile
	uv run gvoice-skill login

session: setup  ## Show session status
	uv run gvoice-skill session

clean:  ## Remove caches
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
