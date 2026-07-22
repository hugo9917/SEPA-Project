.PHONY: help up down logs build test lint backfill dashboard clean

DATE ?= $(shell date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d)
TYPE ?= minorista

help:
	@echo "make up         - build and start the whole stack"
	@echo "make down       - stop the stack (keeps volumes)"
	@echo "make clean      - stop the stack and delete volumes"
	@echo "make logs       - follow all service logs"
	@echo "make test       - run the offline pytest suite"
	@echo "make lint       - run ruff"
	@echo "make backfill   - load a date range (START=... END=... TYPE=...)"

up:
	docker compose up --build -d
	@echo "Airflow  http://localhost:8080 (admin/admin)"
	@echo "MinIO    http://localhost:9001 (minioadmin/minioadmin)"
	@echo "Dashboard http://localhost:8501"

down:
	docker compose down

clean:
	docker compose down -v

logs:
	docker compose logs -f

build:
	docker compose build

test:
	pytest

lint:
	ruff check src tests dags

# Example: make backfill START=2026-07-20 END=2026-07-21 TYPE=minorista
START ?= $(DATE)
END ?= $(DATE)
backfill:
	docker compose run --rm streamlit python -m src.fetch_sepa_range \
		--start-date $(START) --end-date $(END) --type $(TYPE)
