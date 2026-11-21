# Upside LeadHunter - every target runs inside docker compose, so the host
# only needs Docker.
COMPOSE := docker compose
RUN_WEB := $(COMPOSE) run --rm --no-deps web
RUN_WEB_DB := $(COMPOSE) run --rm web

.PHONY: up down logs migrate revision test lint format typecheck arch check seed shell

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f web worker

migrate:
	$(RUN_WEB_DB) alembic upgrade head

revision:
	$(RUN_WEB_DB) alembic revision --autogenerate -m "$(m)"

test:
	$(RUN_WEB_DB) pytest

lint:
	$(RUN_WEB) ruff check app tests
	$(RUN_WEB) ruff format --check app tests

format:
	$(RUN_WEB) ruff format app tests
	$(RUN_WEB) ruff check --fix app tests

typecheck:
	$(RUN_WEB) pyright

arch:
	$(RUN_WEB) lint-imports

check: lint typecheck arch test

seed:
	$(RUN_WEB_DB) python -m app.shared.seed

shell:
	$(RUN_WEB_DB) bash
