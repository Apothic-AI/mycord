.PHONY: help setup lint test run start eval status stop clean

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## Install dependencies
	uv sync --all-extras

lint:  ## Lint and format check
	uv run ruff check src/ tests/
	uv run mypy src/ --strict

test:  ## Run the test suite
	uv run pytest

start:  ## Start the persistent session (logs in)
	uv run mycord-repl start

status:  ## Show session status
	uv run mycord-repl status

stop:  ## Stop the session
	uv run mycord-repl stop

clean:  ## Remove caches
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete