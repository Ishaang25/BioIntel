# BioIntel developer commands.
.DEFAULT_GOAL := help
PY := backend/.venv/bin/python
ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
endif

.PHONY: help setup api worker web dev test test-network lint fmt typecheck migrate migration seed clean docker docker-down

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

test: ## Run the full test suite
	cd backend && ../$(PY) -m pytest -q
	cd frontend && npm test

test-network: ## Include tests that hit the live literature APIs
	cd backend && BIOINTEL_TEST_NETWORK=1 ../$(PY) -m pytest -q -m network

lint: ## Lint the backend
	cd backend && ../$(PY) -m ruff check app tests

fmt: ## Format the backend
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

docker: ## Bring up the full stack in Docker
	docker compose up --build

docker-down: ## Tear the stack down
	docker compose down -v
