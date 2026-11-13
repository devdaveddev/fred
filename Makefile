.PHONY: help env up down logs dev test test-api typecheck reseed psql clean

COMPOSE = docker compose
DEV = $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

help:  ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

env:   ## Create .env from the example if missing
	@test -f .env || (cp .env.example .env && echo "created .env")

up: env    ## Build and run the production stack on http://localhost:8080
	$(COMPOSE) up -d --build
	@echo "→ http://localhost:$${HTTP_PORT:-8080}"

down:  ## Stop the stack (data volume kept)
	$(COMPOSE) down

logs:  ## Tail logs
	$(COMPOSE) logs -f --tail=100

dev: env   ## Hot-reload stack: web :3000, api :8000, db :5432
	$(DEV) up --build

test: test-api typecheck  ## All tests

test-api:  ## Backend unit tests (revcore + metrics fixtures)
	cd backend && uv run pytest -q

typecheck: ## Frontend type check
	cd frontend && npm run typecheck

reseed:  ## Rebuild the demo workspace
	$(COMPOSE) run --rm migrate python -m app.seed --reset

psql:  ## Open psql in the db container
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-fred} $${POSTGRES_DB:-fred}

clean: ## Stop and DELETE all data volumes
	$(COMPOSE) down -v
