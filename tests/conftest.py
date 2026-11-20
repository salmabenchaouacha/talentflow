from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talentflow.config import Settings  # noqa: E402
from talentflow.pipeline import TalentFlow  # noqa: E402

TODAY = date(2026, 11, 20)

CV_TEXT = """Camille Martin
Data Engineer Senior
Email : camille.martin@mail.fr | Tél : 06 12 34 56 78
12 rue des Lilas, 31000 Toulouse
Née le 12/03/1990 — Nationalité : française — Mariée, 2 enfants

Localisation : Toulouse | Mobilité : Toulouse, Full remote
Disponibilité : 01/12/2026
TJM : 520 €

PROFIL
Data engineer orientée industrialisation de pipelines.

COMPÉTENCES
Python, SQL, Airflow, dbt, Snowflake, Docker, Git

EXPÉRIENCES PROFESSIONNELLES
Data Engineer — Aeronov (Toulouse)
03/2021 – Présent
Pipelines batch avec Airflow et dbt sur Snowflake, Python.
Développeuse BI — BanqueSud
01/2017 – 02/2021
ETL Talend, SQL Server, Power BI.

FORMATION
Diplôme d'ingénieur informatique (2016)

LANGUES
Français (natif), Anglais (courant)
"""

NEED_TEXT = """FICHE DE BESOIN — RÉF B-001
Client : Aeronov (secteur aéronautique)
Intitulé : Data Engineer senior
Contexte : Industrialisation de la plateforme data.
Compétences obligatoires : Python, Airflow, dbt, SQL
Compétences souhaitées : Snowflake, Kafka
Expérience : 5 ans minimum
Séniorité : senior
Lieu : Toulouse
Télétravail : 2 jours/semaine (hybride)
Démarrage : 15/12/2026
Durée : 6 mois
TJM max : 550 €
Langues : anglais courant
Contact client : Paul Durand — paul.durand@aeronov.fr — 05 61 22 33 44
"""


class FakeLLM:
    """Faux client LLM : renvoie des réponses prédéfinies, dans l'ordre."""

    def __init__(self, responses: list[str]) -> None:
        from talentflow.llm.clients import LLMClient

        class _Client(LLMClient):
            def __init__(inner):  # noqa: N805
                super().__init__(model="fake", max_retries=2)
                inner.responses = list(responses)
                inner.prompts: list[str] = []

            def _chat(inner, system, user, json_mode):  # noqa: N805
                inner.prompts.append(user)
                return inner.responses.pop(0)

        self.client = _Client()


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.database_url = f"sqlite:///{tmp_path / 'test.db'}"
    s.output_dir = tmp_path / "outputs"
    s.data_dir = tmp_path / "data"
    return s


@pytest.fixture
def flow(settings) -> TalentFlow:
    return TalentFlow.build(settings, today=TODAY)


@pytest.fixture
def fake_llm():
    return lambda responses: FakeLLM([json.dumps(r) if isinstance(r, dict) else r
                                      for r in responses]).client
