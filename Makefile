.PHONY: help up down build logs shell-backend shell-worker migrate reset-db \
        lint-backend format-backend test-backend

help:
	@echo "PaperPilot Dev Commands"
	@echo "  make up              Start all services"
	@echo "  make down            Stop all services"
	@echo "  make build           Rebuild all images"
	@echo "  make logs            Tail logs for all services"
	@echo "  make logs-backend    Tail backend logs"
	@echo "  make logs-worker     Tail worker logs"
	@echo "  make shell-backend   Open shell in backend container"
	@echo "  make shell-worker    Open shell in worker container"
	@echo "  make migrate         Run Alembic migrations"
	@echo "  make reset-db        Drop + recreate DB and rerun migrations"
	@echo "  make format          Run ruff format on backend"
	@echo "  make lint            Run ruff check on backend"

up:
	cp -n .env.example .env 2>/dev/null || true
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

logs-backend:
	docker compose logs -f backend

logs-worker:
	docker compose logs -f worker

shell-backend:
	docker compose exec backend bash

shell-worker:
	docker compose exec worker bash

migrate:
	docker compose exec backend alembic upgrade head

makemigration:
	docker compose exec backend alembic revision --autogenerate -m "$(MSG)"

reset-db:
	docker compose exec postgres psql -U paperpilot -c "DROP DATABASE IF EXISTS paperpilot;"
	docker compose exec postgres psql -U paperpilot -c "CREATE DATABASE paperpilot;"
	$(MAKE) migrate

format:
	docker compose exec backend ruff format app/

lint:
	docker compose exec backend ruff check app/

test-backend:
	docker compose exec backend pytest app/tests/ -v
