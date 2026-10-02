.PHONY: help setup install clean fmt lint typecheck test test-unit test-integration test-cov run build

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## Install development dependencies
	uv sync --all-extras

install:  ## Install production dependencies only
	uv sync

clean:  ## Remove build artifacts and cache files
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

fmt:  ## Format code with black
	uv run black mycord/ tests/

lint:  ## Lint code with ruff
	uv run ruff check mycord/ tests/

typecheck:  ## Type check with mypy
	uv run mypy mycord/ --strict

test:  ## Run all tests (without coverage due to Python 3.12 compatibility)
	uv run pytest tests/ -v --no-cov

test-unit:  ## Run unit tests only
	uv run pytest -m unit -v --no-cov

test-integration:  ## Run integration tests only
	uv run pytest -m integration -v --no-cov

test-cov:  ## Run tests with detailed coverage report (currently disabled due to discord.py-self Python 3.12 issue)
	@echo "Coverage disabled: discord.py-self has Python 3.12 compatibility issues"
	@echo "Run tests without coverage: make test"

run:  ## Run the mycord MCP server (requires DISCORD_TOKEN in .env)
	python -m mycord.app.server

build:  ## Build distribution packages
	python -m build

.DEFAULT_GOAL := help
