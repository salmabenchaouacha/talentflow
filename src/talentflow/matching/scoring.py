"""Scores explicables : couverture de compétences, séniorité, similarité sémantique."""

from __future__ import annotations

from dataclasses import dataclass, field

from talentflow.extraction.skills import relatedness
from talentflow.schemas import CandidateProfile, JobNeed

# Poids du pré-score (avant le juge LLM). Somme = 1.
WEIGHTS = {"coverage": 0.45, "vector": 0.25, "seniority": 0.15, "depth": 0.15}


@dataclass
class SkillCoverage:
    score: float
    matched: list[str] = field(default_factory=list)
    partial: list[tuple[str, str]] = field(default_factory=list)  # (demandé, proche)
    missing: list[str] = field(default_factory=list)
    depth: float = 0.0  # profondeur moyenne (années) sur les compétences obligatoires


def skill_coverage(c: CandidateProfile, n: JobNeed) -> SkillCoverage:
    """Obligatoires = poids 1, souhaitées = poids 0.3 ; crédit partiel pour une techno proche."""
    have = c.skill_names
    total_w, got_w = 0.0, 0.0
    matched, partial, missing = [], [], []
    depth_vals = []

    for weight, skills, mandatory in ((1.0, n.required_skills, True), (0.3, n.nice_to_have, False)):
        for s in skills:
            total_w += weight
            if s in have:
                got_w += weight
                matched.append(s)
                if mandatory:
                    yrs = c.skill_years(s)
                    depth_vals.append(min((yrs or 1.0) / max(n.min_years or 3, 1), 1.0))
                continue
            best, best_r = None, 0.0
            for h in have:
                r = relatedness(s, h)
                if r > best_r:
                    best, best_r = h, r
            if best and best_r > 0:
                got_w += weight * best_r
                partial.append((s, best))
                if mandatory:
                    depth_vals.append(0.25)
                    missing.append(s)
            elif mandatory:
                missing.append(s)
                depth_vals.append(0.0)

    score = got_w / total_w if total_w else 0.0
    depth = sum(depth_vals) / len(depth_vals) if depth_vals else 0.0
    return SkillCoverage(score=round(score, 4), matched=matched, partial=partial,
                         missing=missing, depth=round(depth, 4))


def seniority_fit(c: CandidateProfile, n: JobNeed) -> float:
    """1.0 si niveau atteint ; pénalité douce si en dessous, légère si très au-dessus."""
    target = n.seniority.rank if n.seniority else None
    if target is None and n.min_years:
        from talentflow.schemas import Seniority

        target = Seniority.from_years(n.min_years).rank
    if target is None:
        return 1.0
    diff = c.seniority.rank - target
    if diff >= 0:
        return 1.0 if diff <= 1 else 0.8  # sur-qualifié : risque de TJM / d'ennui en mission
    return max(0.0, 1.0 + 0.5 * diff)


def pre_score(coverage: float, vector: float, seniority: float, depth: float) -> float:
    v = max(0.0, min(1.0, (vector - 0.1) / 0.6))  # recalage des cosinus vers [0, 1]
    return round(
        WEIGHTS["coverage"] * coverage
        + WEIGHTS["vector"] * v
        + WEIGHTS["seniority"] * seniority
        + WEIGHTS["depth"] * depth,
        4,
    )
