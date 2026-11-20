"""Évaluation de bout en bout sur le jeu synthétique annoté.

Mesures :
1. Pseudonymisation : taux de fuite des données personnelles connues (doit être 0 %).
2. Extraction : F1 des compétences, erreur sur les années, exactitude TJM / dispo / ville.
3. Matching : Precision@3, nDCG@5, MRR, rappel de la récupération vectorielle,
   exclusions à tort par les filtres — avec étude d'ablation vector / rules / full.

Toutes les mesures sont relatives au jeu synthétique ; elles servent à détecter
les régressions (CI) et à comparer des variantes, pas à annoncer une performance réelle.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date
from pathlib import Path

from talentflow.config import Settings
from talentflow.extraction.skills import normalize_skill
from talentflow.pipeline import TalentFlow
from talentflow.storage.repository import _json_load


# ---------------------------------------------------------------- métriques
def precision_at_k(ranked: list[str], gold: dict[str, int], k: int) -> float:
    top = ranked[:k]
    return sum(1 for c in top if gold.get(c, 0) > 0) / k if top else 0.0


def ndcg_at_k(ranked: list[str], gold: dict[str, int], k: int) -> float:
    dcg = sum((2 ** gold.get(c, 0) - 1) / math.log2(i + 2) for i, c in enumerate(ranked[:k]))
    ideal = sorted(gold.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def mrr(ranked: list[str], gold: dict[str, int]) -> float:
    for i, c in enumerate(ranked):
        if gold.get(c, 0) > 0:
            return 1 / (i + 1)
    return 0.0


def f1(pred: set[str], truth: set[str]) -> float:
    if not pred and not truth:
        return 1.0
    tp = len(pred & truth)
    if not tp:
        return 0.0
    p, r = tp / len(pred), tp / len(truth)
    return 2 * p * r / (p + r)


def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 3) if xs else None


# --------------------------------------------------------------- évaluation
def run_evaluation(data_dir: str | Path = "data", settings: Settings | None = None,
                   out_dir: str | Path = "reports", db_url: str | None = None) -> dict:
    data_dir = Path(data_dir)
    truth = json.loads((data_dir / "synthetic" / "ground_truth.json").read_text(encoding="utf-8"))
    today = date.fromisoformat(truth["reference_date"])
    settings = settings or Settings()
    settings.database_url = db_url or "sqlite:///:memory:"
    settings.data_dir = data_dir
    flow = TalentFlow.build(settings, today=today)

    # --- ingestion (sha256 -> identifiant interne) ------------------------
    cv_map, need_map = {}, {}
    for gid, info in truth["consultants"].items():
        path = data_dir / "synthetic" / "cvs" / info["file"]
        res = flow.ingest_path(path, kind="cv")
        cv_map[gid] = res.entity_id
    for gid, info in truth["needs"].items():
        path = data_dir / "synthetic" / "needs" / info["file"]
        res = flow.ingest_path(path, kind="need")
        need_map[gid] = res.entity_id
    inv_cv = {v: k for k, v in cv_map.items()}

    # --- 1. pseudonymisation ---------------------------------------------
    leaks, checked = [], 0
    docs = flow.repo.query("SELECT filename, source, pseudo_text FROM documents")
    stored = "\n".join(f"{d['filename']} {d['source']} {d['pseudo_text']}" for d in docs)
    stored += "\n".join(json.dumps(_json_load(r["profile"]), ensure_ascii=False)
                        for r in flow.repo.query("SELECT profile FROM candidates"))
    stored_l = stored.lower()
    for group in ("consultants", "needs"):
        for gid, info in truth[group].items():
            for value in info["pii"]:
                checked += 1
                if value.lower() in stored_l:
                    leaks.append({"id": gid, "value_hash": hashlib.sha1(value.encode()).hexdigest()[:8]})
    privacy = {"pii_values_checked": checked, "leaks": len(leaks),
               "leak_rate": round(len(leaks) / checked, 4) if checked else 0.0}

    # --- 2. extraction ----------------------------------------------------
    skill_f1, year_err, rate_ok, avail_ok, city_ok = [], [], [], [], []
    for gid, info in truth["consultants"].items():
        p = flow.repo.get_candidate(cv_map[gid])
        skill_f1.append(f1(p.skill_names, {normalize_skill(s) for s in info["skills"]}))
        year_err.append(abs(p.years_experience - info["years"]))
        rate_ok.append(float(p.daily_rate == info["rate"]))
        avail_ok.append(float(p.availability_date is not None
                              and p.availability_date.isoformat() == info["availability"]))
        city_ok.append(float((p.location or "").lower() == info["city"].lower()))
    need_f1, need_years, need_rate = [], [], []
    for gid, info in truth["needs"].items():
        n = flow.repo.get_need(need_map[gid])
        need_f1.append(f1(set(n.required_skills), {normalize_skill(s) for s in info["required"]}))
        need_years.append(float(n.min_years == info["min_years"]))
        need_rate.append(float(n.max_daily_rate == info["max_rate"]))
    extraction = {
        "extractor": flow.extractor.name,
        "cv_skills_f1": _mean(skill_f1), "cv_years_mae": _mean(year_err),
        "cv_rate_accuracy": _mean(rate_ok), "cv_availability_accuracy": _mean(avail_ok),
        "cv_city_accuracy": _mean(city_ok), "need_required_skills_f1": _mean(need_f1),
        "need_min_years_accuracy": _mean(need_years), "need_max_rate_accuracy": _mean(need_rate),
    }

    # --- 3. matching (ablation) ------------------------------------------
    matching = {}
    per_need = []
    for mode in ("vector", "rules", "full"):
        p3, nd5, rr, rec_ret, wrong_excl, prop_prec = [], [], [], [], [], []
        for gid, nid in need_map.items():
            gold_g = truth["gold"][gid]
            if not any(v > 0 for v in gold_g.values()):
                continue
            gold = {cv_map[c]: g for c, g in gold_g.items()}
            report = flow.match_need(nid, mode=mode, persist=False)
            ranked = [r.candidate_id for r in report.ranked]
            p3.append(precision_at_k(ranked, gold, 3))
            nd5.append(ndcg_at_k(ranked, gold, 5))
            rr.append(mrr(ranked, gold))
            retrieved = {r.candidate_id for r in report.ranked + report.excluded}
            relevant = {c for c, g in gold.items() if g > 0}
            rec_ret.append(len(relevant & retrieved) / len(relevant))
            excl = {r.candidate_id for r in report.excluded}
            if mode != "vector":
                wrong_excl.append(len(relevant & excl) / len(relevant))
            props = [r.candidate_id for r in report.proposals]
            if props:
                prop_prec.append(sum(1 for c in props if gold.get(c, 0) > 0) / len(props))
            if mode == "full":
                per_need.append({
                    "need": gid, "title": truth["needs"][gid]["title"],
                    "relevant": len(relevant),
                    "proposed": [inv_cv[c] for c in props],
                    "proposed_grades": [gold.get(c, 0) for c in props],
                })
        matching[mode] = {
            "needs_evaluated": len(p3), "precision_at_3": _mean(p3), "ndcg_at_5": _mean(nd5),
            "mrr": _mean(rr), "proposal_precision": _mean(prop_prec),
            "retrieval_recall": _mean(rec_ret),
            "wrongly_excluded_rate": _mean(wrong_excl) if wrong_excl else None,
        }

    llm_usage = None
    if flow.llm:
        u = flow.llm.usage
        llm_usage = {"calls": u.calls, "prompt_tokens": u.prompt_tokens,
                     "completion_tokens": u.completion_tokens,
                     "median_latency_s": round(statistics.median(u.latency_s), 2)
                     if u.latency_s else None,
                     "validation_retries": u.validation_retries}

    result = {
        "dataset": {"consultants": len(cv_map), "needs": len(need_map),
                    "reference_date": truth["reference_date"], "seed": truth["seed"]},
        "config": {"llm_provider": settings.llm_provider, "llm_model": settings.llm_model
                   if settings.llm_provider != "offline" else None,
                   "embedding_provider": settings.embedding_provider,
                   "retrieval_k": settings.retrieval_k, "judge_top_n": settings.judge_top_n,
                   "judge_weight": settings.judge_weight},
        "privacy": privacy, "extraction": extraction, "matching": matching,
        "per_need": per_need, "llm_usage": llm_usage,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
    (out / "evaluation.md").write_text(to_markdown(result), encoding="utf-8")
    return result


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def to_markdown(r: dict) -> str:
    m = r["matching"]
    lines = [
        "# Rapport d'évaluation TalentFlow",
        "",
        f"Jeu synthétique : {r['dataset']['consultants']} consultants, {r['dataset']['needs']} "
        f"besoins (graine {r['dataset']['seed']}, date de référence {r['dataset']['reference_date']}).",
        f"Configuration : LLM `{r['config']['llm_provider']}`"
        + (f" ({r['config']['llm_model']})" if r['config']['llm_model'] else "")
        + f", embeddings `{r['config']['embedding_provider']}`.",
        "",
        "> Résultats sur données synthétiques, à usage de non-régression et de comparaison "
        "de variantes. Les jugements de pertinence sont à relire à la main "
        "(`data/eval/annotations_a_relire.csv`).",
        "",
        "## 1. Pseudonymisation (RGPD)",
        f"- Valeurs personnelles vérifiées : {r['privacy']['pii_values_checked']}",
        f"- Fuites dans les données stockées / envoyées au LLM : **{r['privacy']['leaks']}** "
        f"(taux {r['privacy']['leak_rate']:.2%})",
        "",
        "## 2. Extraction",
        "| Mesure | Valeur |",
        "|---|---|",
    ]
    for k, v in r["extraction"].items():
        lines.append(f"| {k} | {_fmt(v)} |")
    lines += [
        "",
        "## 3. Matching — étude d'ablation",
        "| Mode | Precision@3 | nDCG@5 | MRR | Précision des propositions | Rappel récupération | Exclus à tort |",
        "|---|---|---|---|---|---|---|",
    ]
    names = {"vector": "Similarité seule", "rules": "+ filtres métier & pré-score",
             "full": "+ LLM-as-a-judge"}
    for mode in ("vector", "rules", "full"):
        x = m[mode]
        lines.append(f"| {names[mode]} | {_fmt(x['precision_at_3'])} | {_fmt(x['ndcg_at_5'])} | "
                     f"{_fmt(x['mrr'])} | {_fmt(x['proposal_precision'])} | "
                     f"{_fmt(x['retrieval_recall'])} | {_fmt(x['wrongly_excluded_rate'])} |")
    lines += ["", "## 4. Détail par besoin (mode complet)", "",
              "| Besoin | Profils pertinents | Proposés (pertinence 0/1/2) |", "|---|---|---|"]
    for p in r["per_need"]:
        props = ", ".join(f"{c} ({g})" for c, g in zip(p["proposed"], p["proposed_grades"]))
        lines.append(f"| {p['need']} · {p['title']} | {p['relevant']} | {props or '—'} |")
    if r.get("llm_usage"):
        lines += ["", "## 5. Coût LLM", "```", json.dumps(r["llm_usage"], indent=2), "```"]
    return "\n".join(lines) + "\n"
