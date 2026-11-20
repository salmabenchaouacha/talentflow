"""Garde-fous de non-régression sur le jeu synthétique (exécutés en CI)."""

from conftest import TODAY

from talentflow.config import Settings
from talentflow.evaluation.evaluate import mrr, ndcg_at_k, precision_at_k, run_evaluation
from talentflow.evaluation.synthetic import generate


def test_ranking_metrics():
    gold = {"a": 2, "b": 0, "c": 1}
    assert precision_at_k(["a", "b", "c"], gold, 3) == 2 / 3
    assert mrr(["b", "c"], gold) == 0.5
    assert ndcg_at_k(["a", "c", "b"], gold, 3) == 1.0


def test_end_to_end_quality_gates(tmp_path):
    data = tmp_path / "data"
    generate(data, n_consultants=40, n_needs=6, seed=7, today=TODAY)
    res = run_evaluation(data, Settings(), tmp_path / "reports")

    assert res["privacy"]["leaks"] == 0
    assert res["extraction"]["cv_skills_f1"] >= 0.9
    full, vector = res["matching"]["full"], res["matching"]["vector"]
    assert full["precision_at_3"] >= 0.6
    assert full["ndcg_at_5"] >= vector["ndcg_at_5"]
    assert (tmp_path / "reports" / "evaluation.md").exists()
