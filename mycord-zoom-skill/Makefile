.PHONY: help setup lint typecheck test test-live verify run audio-up audio-down audio-status clean

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## Install dependencies
	uv sync --all-extras

lint:  ## Lint and format check
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:  ## Static type check (strict)
	uv run mypy src/

test:  ## Run the offline test suite (no audio server, no Zoom client)
	uv run pytest -m "not live"

test-live:  ## Run the tests that use the real audio server (creates and removes real devices)
	uv run pytest -m live -s

verify: lint typecheck test  ## Everything CI would run, except the live tests

run: setup  ## Run the CLI: make run CMD="audio status"
	uv run mycord-zoom $(CMD)

audio-up: setup  ## Create the virtual devices (records the operator's defaults first)
	uv run mycord-zoom audio up

audio-status: setup  ## Show what is loaded and where audio points
	uv run mycord-zoom --json audio status

audio-down: setup  ## Restore the operator's defaults and unload the virtual devices
	uv run mycord-zoom audio down

clean:  ## Remove caches
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
