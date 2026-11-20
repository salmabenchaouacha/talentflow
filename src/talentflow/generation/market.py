"""Veille marché : quelles compétences sont demandées, et le vivier peut-il y répondre ?

Sources : besoins clients reçus + offres d'emploi publiques (CSV ; en production,
API Offres d'emploi de France Travail ou export d'un job board).
Sorties : tendances de la demande, analyse d'écart demande/vivier → compétences à
sourcer en priorité et compétences disponibles à pousser en prospection.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from talentflow.extraction.skills import normalize_skill
from talentflow.schemas import CandidateProfile, JobNeed


@dataclass
class JobPost:
    published: date
    company: str
    sector: str
    city: str
    title: str
    skills: list[str]


def load_job_posts(path: str | Path) -> list[JobPost]:
    p = Path(path)
    if not p.exists():
        return []
    posts = []
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            posts.append(JobPost(
                published=date.fromisoformat(row["published"]),
                company=row["company"],
                sector=row["sector"],
                city=row["city"],
                title=row["title"],
                skills=[normalize_skill(s) for s in row["skills"].split("|") if s],
            ))
    return posts


def skill_demand(needs: list[JobNeed], posts: list[JobPost], since: date | None = None,
                 until: date | None = None) -> Counter:
    demand: Counter = Counter()
    for n in needs:
        for s in n.required_skills:
            demand[s] += 2.0  # un besoin client réel pèse plus qu'une annonce publique
        for s in n.nice_to_have:
            demand[s] += 0.5
    for p in posts:
        if (since and p.published < since) or (until and p.published >= until):
            continue
        for s in p.skills:
            demand[s] += 1.0
    return demand


def pool_supply(candidates: list[CandidateProfile], today: date,
                available_within_days: int = 60) -> Counter:
    limit = today + timedelta(days=available_within_days)
    supply: Counter = Counter()
    for c in candidates:
        if c.availability_date and c.availability_date > limit:
            continue
        for s in c.skills:
            supply[s.name] += 1
    return supply


def gap_analysis(demand: Counter, supply: Counter, top: int = 15) -> list[dict]:
    total_d = sum(demand.values()) or 1
    total_s = sum(supply.values()) or 1
    rows = []
    for skill, d in demand.most_common(top * 2):
        share_d = d / total_d
        share_s = supply.get(skill, 0) / total_s
        ratio = share_s / share_d if share_d else 0
        status = ("à sourcer" if ratio < 0.5 else "à pousser en prospection" if ratio > 1.2
                  else "équilibré")
        rows.append({
            "skill": skill,
            "demand_score": round(d, 1),
            "available_consultants": supply.get(skill, 0),
            "coverage_ratio": round(ratio, 2),
            "action": status,
        })
    return rows[:top]


def trends(posts: list[JobPost], today: date, window_days: int = 28, top: int = 10) -> list[dict]:
    recent = skill_demand([], posts, since=today - timedelta(days=window_days), until=today)
    before = skill_demand([], posts, since=today - timedelta(days=2 * window_days),
                          until=today - timedelta(days=window_days))
    out = []
    for skill, n in recent.most_common(top):
        prev = before.get(skill, 0)
        out.append({
            "skill": skill,
            "posts_recent": int(n),
            "posts_previous": int(prev),
            "evolution_pct": round((n - prev) / prev * 100) if prev else None,
        })
    return out
