.PHONY: help setup lint typecheck test test-live gates doctor versions provision clean

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## Install dependencies
	uv sync --all-extras

lint:  ## Lint and format check
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:  ## Apply formatting
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

typecheck:  ## Static type check (strict) over src/
	uv run mypy src/

test:  ## Run the offline suite (no daemon, no account, no real binaries)
	uv run pytest -m "not live"

test-live:  ## Run every test, including the ones that need the live daemon
	uv run pytest

gates: lint typecheck test  ## Everything CI must pass

doctor: setup  ## Report versions, pipe readiness, daemon and account state
	uv run mycord-signal doctor

versions: setup  ## Print measured versions as JSON
	uv run mycord-signal versions

provision:  ## Detect stale pins and print the remediation (add APPLY=1 to act)
	scripts/provision.sh $(if $(APPLY),--apply,)

clean:  ## Remove caches
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/ build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
