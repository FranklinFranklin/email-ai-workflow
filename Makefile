.PHONY: help install dev test lint format migrate docker-up docker-down

help:  ## Toon beschikbare commando's
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Installeer dependencies
	pip install -e ".[dev]"
	python -m spacy download nl_core_news_sm
	python -m spacy download en_core_web_sm

dev:  ## Start ontwikkelserver met hot-reload
	uvicorn main:app --reload --host 0.0.0.0 --port 8000

worker:  ## Start RQ worker
	rq worker --with-scheduler email_processing

test:  ## Voer alle tests uit
	pytest tests/ -v

test-unit:  ## Alleen unit-tests
	pytest tests/unit/ -v

test-integration:  ## Alleen integratietests
	pytest tests/integration/ -v

lint:  ## Controleer code-kwaliteit
	ruff check .
	mypy . --ignore-missing-imports

format:  ## Formatteer code
	ruff format .
	ruff check --fix .

migrate:  ## Voer database-migraties uit
	alembic upgrade head

migrate-create:  ## Maak nieuwe migratie (gebruik: make migrate-create MSG="beschrijving")
	alembic revision --autogenerate -m "$(MSG)"

migrate-down:  ## Rol laatste migratie terug
	alembic downgrade -1

docker-up:  ## Start alle Docker-services
	docker compose up -d

docker-up-dev:  ## Start alle Docker-services inclusief dev-tools
	docker compose --profile dev up -d

docker-down:  ## Stop alle Docker-services
	docker compose down

docker-logs:  ## Bekijk logs
	docker compose logs -f

docker-rebuild:  ## Herbouw en herstart containers
	docker compose down && docker compose build --no-cache && docker compose up -d

db-shell:  ## Open PostgreSQL-shell
	docker compose exec postgres psql -U emailai -d emailai_db

redis-cli:  ## Open Redis CLI
	docker compose exec redis redis-cli -a $$(grep REDIS_PASSWORD .env | cut -d= -f2)

clean:  ## Verwijder tijdelijke bestanden
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -delete
	find . -type d -name ".mypy_cache" -delete
	find . -type d -name "htmlcov" -delete
