# BioIntel developer commands.
.DEFAULT_GOAL := help
PY := backend/.venv/bin/python
ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
endif

.PHONY: help install setup api worker web run test test-network test-unit test-integration lint fmt typecheck migrate migration seed clean docker docker-down benchmark benchmark-accept regression check profile generate-report

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "\033[36m%-16s\033[0m %s\n",$$1,$$2}'

install: ## Create the virtualenv and install everything
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	cd backend && ../$(PY) -m pip install -e ".[dev]"
	cd frontend && npm install

setup: install ## Alias for `install`

run: ## Run the API and the worker together (Ctrl-C stops both)
	$(PY) scripts/run_stack.py

api: ## Run the API with reload
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000

worker: ## Run the background worker
	cd backend && ../$(PY) -m app.jobs.worker

web: ## Run the frontend dev server
	cd frontend && npm run dev

test: ## Run the full test suite (unit + integration)
	cd backend && ../$(PY) -m pytest -q -m "not benchmark"
	cd frontend && npm test

test-unit: ## Run backend unit tests only
	cd backend && ../$(PY) -m pytest tests/unit/ -q

test-integration: ## Run backend integration tests only
	cd backend && ../$(PY) -m pytest tests/integration/ -q

test-network: ## Include tests that hit the live literature APIs
	cd backend && BIOINTEL_TEST_NETWORK=1 ../$(PY) -m pytest -q -m network

regression: ## Run regression tests for benchmark companies (BioNTech, Moderna)
	cd backend && ../$(PY) -m pytest tests/integration/test_biontech_regression.py tests/integration/test_moderna_regression.py -v

check: ## Run all quality checks (lint + format + typecheck)
	$(MAKE) lint typecheck

lint: ## Lint and format-check the backend (same scope as CI)
	cd backend && ../$(PY) -m ruff check app tests
	cd backend && ../$(PY) -m ruff format --check app tests

fmt: ## Format and fix the backend code
	cd backend && ../$(PY) -m ruff format app tests && ../$(PY) -m ruff check --fix app tests

typecheck: ## Type-check both sides
	cd backend && ../$(PY) -m mypy app
	cd frontend && npm run typecheck

migrate: ## Apply database migrations
	cd backend && ../$(PY) -m alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add x"
	cd backend && ../$(PY) -m alembic revision --autogenerate -m "$(m)"

seed: ## Analyse the bundled sample deck end to end
	cd backend && ../$(PY) -m app.scripts.seed

generate-report: ## Analyse a deck and write its report: make generate-report pdf=deck.pdf
	@test -n "$(pdf)" || (echo "usage: make generate-report pdf=path/to/deck.pdf" && exit 1)
	cd backend && ../$(PY) -m app.scripts.seed --pdf "../$(pdf)"

clean: ## Remove caches and local state
	$(PY) scripts/clean.py

benchmark: ## Capture all 5 benchmarks and check them against benchmarks/baseline.json
	cd backend && ../$(PY) -m pytest tests/benchmarks -q
	@echo "Capture written to benchmarks/results.json"

benchmark-accept: ## Re-record benchmarks/baseline.json from the current code
	cd backend && ../$(PY) -m pytest tests/benchmarks -q || true
	$(PY) -c "import shutil; shutil.copyfile('benchmarks/results.json', 'benchmarks/baseline.json')"
	@echo "Baseline re-recorded. Explain the movement in your commit message."

profile: ## Generate a timing profile of the seed run
	cd backend && ../$(PY) -c "import cProfile; cProfile.run('from app.scripts.seed import main; main()', sort='cumtime')" | head -30

docker: ## Bring up the full stack in Docker
	docker compose up --build

docker-down: ## Tear the stack down
	docker compose down -v

.PHONY: $(MAKECMDGOALS)
