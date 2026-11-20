"""Indicateurs du tableau de bord hebdomadaire (calculés en Python, portables).

En production, les mêmes indicateurs sont modélisés dans dbt (`fct_weekly_kpis`) pour
Metabase / Power BI ; ce module sert à l'API, au récapitulatif n8n et au Streamlit.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime

from talentflow.storage.repository import Repository, _json_load

# Hypothèse explicite, modifiable : temps manuel moyen pour traiter un besoin
# (lecture du vivier, présélection, mise au format du dossier). À mesurer chez le client.
MANUAL_MINUTES_PER_NEED = 120
MANUAL_MINUTES_PER_DOSSIER = 30


def _week(ts: str | None) -> str | None:
    if not ts:
        return None
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    y, w, _ = d.isocalendar()
    return f"{y}-S{w:02d}"


def _hours(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    da = datetime.fromisoformat(a.replace("Z", "+00:00"))
    db = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return (db - da).total_seconds() / 3600


def weekly_kpis(repo: Repository, weeks: int = 8) -> list[dict]:
    needs = repo.query("SELECT need_id, received_at, matched_at FROM needs")
    docs = repo.query("SELECT kind, received_at FROM documents")
    matches = repo.query(
        "SELECT status, final_score, judge_score, created_at, reviewed_at FROM matches"
    )

    buckets: dict[str, dict] = defaultdict(lambda: {
        "needs_received": 0, "cvs_ingested": 0, "proposals": 0, "validated": 0,
        "rejected": 0, "sent": 0, "_scores": [], "_judge": [], "_t_match_min": [],
        "_t_review_h": [],
    })
    for n in needs:
        b = buckets[_week(n["received_at"])]
        b["needs_received"] += 1
        h = _hours(n["received_at"], n["matched_at"])
        if h is not None:
            b["_t_match_min"].append(h * 60)
    for d in docs:
        if d["kind"] == "cv":
            buckets[_week(d["received_at"])]["cvs_ingested"] += 1
    for m in matches:
        b = buckets[_week(m["created_at"])]
        b["proposals"] += 1
        if m["status"] in {"validated", "sent"}:
            b["validated"] += 1
        if m["status"] == "rejected":
            b["rejected"] += 1
        if m["status"] == "sent":
            b["sent"] += 1
        if m["final_score"] is not None:
            b["_scores"].append(float(m["final_score"]))
        if m["judge_score"] is not None:
            b["_judge"].append(float(m["judge_score"]))
        h = _hours(m["created_at"], m["reviewed_at"])
        if h is not None:
            b["_t_review_h"].append(h)

    out = []
    for week in sorted(k for k in buckets if k)[-weeks:]:
        b = buckets[week]
        decided = b["validated"] + b["rejected"]
        out.append({
            "week": week,
            "needs_received": b["needs_received"],
            "cvs_ingested": b["cvs_ingested"],
            "proposals": b["proposals"],
            "validated": b["validated"],
            "rejected": b["rejected"],
            "sent": b["sent"],
            "validation_rate": round(b["validated"] / decided, 3) if decided else None,
            "avg_final_score": round(statistics.mean(b["_scores"]), 3) if b["_scores"] else None,
            "avg_judge_score": round(statistics.mean(b["_judge"]), 1) if b["_judge"] else None,
            "median_minutes_to_proposal": (round(statistics.median(b["_t_match_min"]), 1)
                                           if b["_t_match_min"] else None),
            "median_hours_to_review": (round(statistics.median(b["_t_review_h"]), 1)
                                       if b["_t_review_h"] else None),
            "estimated_hours_saved": round(
                (b["needs_received"] * MANUAL_MINUTES_PER_NEED
                 + b["validated"] * MANUAL_MINUTES_PER_DOSSIER) / 60, 1),
        })
    return out


def rejection_reasons(repo: Repository) -> list[tuple[str, int]]:
    """Motifs de rejet saisis par les commerciaux : base d'amélioration du matching."""
    rows = repo.query("SELECT review_comment FROM matches WHERE status = 'rejected'")
    counter = Counter((r["review_comment"] or "sans motif").strip().lower() for r in rows)
    return counter.most_common(10)


def pool_overview(repo: Repository) -> dict:
    rows = repo.query("SELECT seniority, profile FROM candidates")
    seniority = Counter(r["seniority"] for r in rows)
    skills = Counter()
    for r in rows:
        for s in _json_load(r["profile"]).get("skills", []):
            skills[s["name"]] += 1
    return {
        "candidates": len(rows),
        "by_seniority": dict(seniority),
        "top_skills": skills.most_common(15),
    }
