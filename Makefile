# BioIntel developer commands.
.DEFAULT_GOAL := help
PY := backend/.venv/bin/python
ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
endif

.PHONY: help setup api worker web dev test test-network test-unit test-integration lint fmt typecheck migrate migration seed clean docker docker-down benchmark regression check profile generate-report

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "\033[36m%-16s\033[0m %s\n",$$1,$$2}'

setup: ## Create the virtualenv and install everything
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	cd backend && ../$(PY) -m pip install -e ".[dev]"
	cd frontend && npm install

api: ## Run the API with reload
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --port 8000

worker: ## Run the background worker
	cd backend && ../$(PY) -m app.jobs.worker

web: ## Run the frontend dev server
	cd frontend && npm run dev

test: ## Run the full test suite (unit + integration)
	cd backend && ../$(PY) -m pytest -q
	cd frontend && npm test

test-unit: ## Run backend unit tests only
	cd backend && ../$(PY) -m pytest tests/unit/ -q

test-integration: ## Run backend integration tests only
	cd backend && ../$(PY) -m pytest tests/integration/ -q

test-network: ## Include tests that hit the live literature APIs
	cd backend && BIOINTEL_TEST_NETWORK=1 ../$(PY) -m pytest -q -m network

regression: ## Run regression tests for benchmark companies (BioNTech, Moderna)
	cd backend && ../$(PY) -m pytest tests/integration/test_biontech_regression.py tests/integration/test_moderna_regression.py -v

check: ## Run all quality checks (lint + typecheck)
	$(MAKE) lint typecheck

lint: ## Lint the backend
	cd backend && ../$(PY) -m ruff check app tests

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

clean: ## Remove caches and local state
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
	rm -rf frontend/.next storage/*.db storage/*.db-wal storage/*.db-shm
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

benchmark: ## Run all benchmarks and generate reports
	$(MAKE) regression
	@echo "Benchmark run complete. Metrics saved to storage/metrics/"

profile: ## Generate a timing profile of the seed run
	cd backend && ../$(PY) -c "import cProfile; cProfile.run('from app.scripts.seed import main; main()', sort='cumtime')" | head -30

docker: ## Bring up the full stack in Docker
	docker compose up --build

docker-down: ## Tear the stack down
	docker compose down -v

.PHONY: $(MAKECMDGOALS)
