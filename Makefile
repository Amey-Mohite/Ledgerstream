# =============================================================================
# Ledgerstream — common tasks
#
# Windows note: `make` isn't installed by default. Either install it
# (`choco install make`) or run the underlying `docker compose ...` commands
# shown in each target. Everything here is a thin wrapper over docker compose.
# =============================================================================

COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help env up full-up down restart ps logs health clean shared-install shared-test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from template if missing
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

up: env ## DAILY DEV: start Kafka + Schema Registry + observability (data stores = cloud)
	$(COMPOSE) up -d --wait

full-up: env ## OFFLINE DEMO: also start local postgres/mongo/redis (--profile full)
	$(COMPOSE) --profile full up -d --wait

down: ## Stop the stack (keeps volumes/data). Add profile to catch full-mode containers.
	$(COMPOSE) --profile full down

restart: down up ## Restart the daily-dev stack

ps: ## Show container status
	$(COMPOSE) --profile full ps

logs: ## Tail logs from all services (Ctrl-C to stop)
	$(COMPOSE) logs -f --tail=100

health: ## Print the health state of every container
	$(COMPOSE) --profile full ps --format 'table {{.Name}}\t{{.Status}}'

clean: ## Stop the stack and DELETE all data volumes (destructive)
	$(COMPOSE) --profile full down -v

shared-install: ## Install the shared library (editable) with dev extras
	pip install -e libs/shared[dev]

shared-test: ## Run the shared-library unit tests
	pytest libs/shared -v
