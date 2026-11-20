.PHONY: install test lint demo eval api ui up up-batch down n8n-import clean

PY ?= python3
export PYTHONPATH := src

install:          ## Installe le paquet et les dépendances de dev
	$(PY) -m pip install -e ".[all]"

test:             ## Tests unitaires et d'intégration
	$(PY) -m pytest -q

lint:
	ruff check src tests dashboard airflow

demo:             ## Démo complète hors-ligne (aucune clé d'API nécessaire)
	$(PY) -m talentflow.cli demo

eval:             ## Rapport d'évaluation → reports/evaluation.md
	$(PY) -m talentflow.cli eval --min-precision 0.6

api:              ## API locale (SQLite) sur http://localhost:8000/docs
	uvicorn talentflow.api.main:app --reload

ui:               ## Interface commerciale sur http://localhost:8501
	streamlit run dashboard/app.py

up:               ## Pile Docker : Postgres+PGVector, API, interface, n8n, Mailpit
	@test -f .env || cp .env.example .env
	docker compose up -d --build

up-batch:         ## + Airflow et Metabase
	docker compose --profile batch up -d --build

n8n-import:       ## Importe les 4 workflows dans n8n
	docker compose exec n8n n8n import:workflow --separate --input=/workflows

down:
	docker compose down

clean:
	rm -rf outputs reports data/talentflow.db .pytest_cache
