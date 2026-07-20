# DomainGuard developer commands. See CLAUDE.md for the canonical list.
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down build logs migrate test lint fmt seed

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the full stack (docker compose)
	$(COMPOSE) up --build -d

down: ## Stop the stack and remove containers
	$(COMPOSE) down

build: ## Build images
	$(COMPOSE) build

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

migrate: ## Apply database migrations (alembic upgrade head)
	$(COMPOSE) run --rm migrate

test: ## Run the test suite with coverage
	pytest

lint: ## Ruff lint + format check
	ruff check .
	ruff format --check .

fmt: ## Auto-format with ruff
	ruff format .
	ruff check --fix .

seed: ## Seed demo data (2 companies, projects, sample domains)
	$(COMPOSE) run --rm api python -m scripts.seed
