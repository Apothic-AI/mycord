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

run: setup  ## Start the session (detached)
	uv run mycord-telegram-repl start

login:  ## Authenticate a user account (METHOD=desktop|qr|phone)
	uv run mycord-telegram-repl login --method $(or $(METHOD),desktop)

start: setup  ## Start the session (detached)
	uv run mycord-telegram-repl start

eval:  ## Evaluate a snippet: make eval CODE="1 + 1"
	uv run mycord-telegram-repl eval "$(CODE)"

status:  ## Report session status
	uv run mycord-telegram-repl status

stop:  ## Shut the session down
	uv run mycord-telegram-repl stop

clean:  ## Remove caches
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete