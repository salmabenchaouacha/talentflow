"""DAGs Airflow de TalentFlow : les traitements de fond, planifiés et rejouables.

Le temps réel (un besoin arrive → profils proposés en quelques minutes) passe par n8n.
Airflow porte ce qui doit tourner de façon fiable, avec reprise et historique :
- `talentflow_daily_catchup`  : rattrapage des documents déposés dans le dossier d'entrée
  (pièces jointes manquées, imports manuels) ;
- `talentflow_weekly`         : synchronisation du vivier Boond, ré-indexation des
  embeddings, purge RGPD, évaluation de non-régression, modèles dbt du tableau de bord,
  instantané de la veille marché.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

from airflow import DAG

DEFAULT_ARGS = {
    "owner": "talentflow",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}
DBT_DIR = "/opt/talentflow/dbt"


def _flow():
    from talentflow.config import get_settings
    from talentflow.pipeline import TalentFlow

    return TalentFlow.build(get_settings())


def ingest_dropzone(**_):
    from talentflow.ingestion.sources import iter_folder

    flow = _flow()
    root = flow.settings.data_dir / "inbox"
    done = root / "processed"
    done.mkdir(parents=True, exist_ok=True)
    counts = {"cv": 0, "need": 0, "duplicates": 0}
    for doc in iter_folder(root / "cvs", kind="cv"):
        res = flow.ingest(doc.data, doc.filename, kind="cv", source="airflow-dropzone")
        counts["duplicates" if res.duplicate else "cv"] += 1
    for doc in iter_folder(root / "needs", kind="need"):
        res = flow.ingest(doc.data, doc.filename, kind="need", source="airflow-dropzone")
        if res.duplicate:
            counts["duplicates"] += 1
        else:
            counts["need"] += 1
            flow.match_need(res.entity_id, actor="airflow")
    return counts


def sync_boond(**_):
    from talentflow.connectors.boond import BoondClient, resource_to_text

    flow = _flow()
    s = flow.settings
    if not s.boond_base_url:
        return "Boond non configuré (TF_BOOND_BASE_URL) : étape ignorée"
    client = BoondClient(s.boond_base_url, s.boond_client_token, s.boond_user_token,
                         s.boond_client_key)
    new = 0
    for res in client.list_resources(max_results=500):
        out = flow.ingest(resource_to_text(res).encode(), f"boond_{res['id']}.txt", kind="cv",
                          source="boond", actor="airflow")
        new += 0 if out.duplicate else 1
    return f"{new} ressource(s) importée(s)"


def reembed(**_):
    return _flow().reembed_all()


def gdpr_purge(**_):
    return _flow().purge_expired(actor="airflow-retention")


def evaluation(**_):
    from talentflow.evaluation.evaluate import run_evaluation

    res = run_evaluation("/opt/talentflow/data", out_dir="/opt/talentflow/reports")
    if res["privacy"]["leaks"]:
        raise ValueError("Fuite de données personnelles détectée par l'évaluation")
    return res["matching"]["full"]


def market_snapshot(ds: str, **_):
    flow = _flow()
    out = flow.settings.output_dir / "market"
    out.mkdir(parents=True, exist_ok=True)
    path = Path(out, f"market_{ds}.json")
    path.write_text(json.dumps(flow.market_report(), ensure_ascii=False, indent=2), "utf-8")
    return str(path)


with DAG(
    dag_id="talentflow_daily_catchup",
    description="Rattrapage quotidien des documents déposés dans data/inbox",
    schedule="0 7 * * 1-5",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["talentflow"],
) as daily:
    PythonOperator(task_id="ingest_dropzone", python_callable=ingest_dropzone)


with DAG(
    dag_id="talentflow_weekly",
    description="Vivier Boond, embeddings, RGPD, évaluation, dbt, veille marché",
    schedule="0 2 * * 1",  # lundi 2h, avant le bilan n8n de 8h
    start_date=datetime(2026, 11, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["talentflow"],
) as weekly:
    t_sync = PythonOperator(task_id="sync_boond", python_callable=sync_boond)
    t_embed = PythonOperator(task_id="reembed_candidates", python_callable=reembed)
    t_purge = PythonOperator(task_id="gdpr_retention_purge", python_callable=gdpr_purge)
    t_eval = PythonOperator(task_id="regression_evaluation", python_callable=evaluation)
    t_dbt_run = BashOperator(
        task_id="dbt_run", bash_command=f"cd {DBT_DIR} && dbt run --profiles-dir {DBT_DIR}")
    t_dbt_test = BashOperator(
        task_id="dbt_test", bash_command=f"cd {DBT_DIR} && dbt test --profiles-dir {DBT_DIR}")
    t_market = PythonOperator(task_id="market_snapshot", python_callable=market_snapshot)

    t_sync >> t_embed >> t_purge >> [t_eval, t_dbt_run]
    t_dbt_run >> t_dbt_test >> t_market
