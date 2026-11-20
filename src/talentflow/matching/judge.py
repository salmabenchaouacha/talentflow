"""LLM-as-a-judge : note sur 100 + justification lisible par le commercial.

Le juge ne voit que des données pseudonymisées (profil structuré, pas le CV brut) et une
grille de notation fixe. `HeuristicJudge` reproduit la grille sans LLM (hors-ligne/CI).
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from talentflow.llm.clients import LLMClient, LLMError
from talentflow.llm.prompts import JUDGE_SYSTEM
from talentflow.matching.scoring import SkillCoverage
from talentflow.schemas import CandidateProfile, FilterResult, JobNeed, JudgeVerdict

log = logging.getLogger(__name__)


class Judge(Protocol):
    name: str

    def evaluate(self, c: CandidateProfile, n: JobNeed, cov: SkillCoverage,
                 sen_fit: float, filters: FilterResult) -> JudgeVerdict: ...


def _recommendation(score: int) -> str:
    return "proposer" if score >= 70 else "a_etudier" if score >= 50 else "ecarter"


class HeuristicJudge:
    name = "heuristic"

    def evaluate(self, c, n, cov, sen_fit, filters) -> JudgeVerdict:
        practical = max(0.0, 1.0 - 0.25 * len(filters.warnings))
        nice_total = len(n.nice_to_have) or 1
        nice_hit = len([s for s in n.nice_to_have if s in cov.matched]) / nice_total
        req_total = len(n.required_skills) or 1
        req_hit = len([s for s in n.required_skills if s in cov.matched]) / req_total
        score = round(100 * (0.40 * (0.6 * req_hit + 0.4 * cov.depth) + 0.20 * cov.score
                             + 0.15 * sen_fit + 0.10 * nice_hit + 0.15 * practical))
        score = int(max(0, min(100, score)))

        strengths = []
        for s in [s for s in n.required_skills if s in cov.matched][:3]:
            yrs = c.skill_years(s)
            strengths.append(f"{s} ({yrs:g} ans)" if yrs else s)
        if sen_fit >= 1.0:
            strengths.append(f"Séniorité adaptée ({c.seniority.label}, {c.years_experience:g} ans)")
        gaps = [f"{s} non mentionné" for s in cov.missing[:3]]
        gaps += [f"{a} : proche via {b}" for a, b in cov.partial[:2]]
        gaps += filters.warnings[:2]

        missing_txt = (f" Manque : {', '.join(cov.missing[:3])}." if cov.missing
                       else " Toutes les compétences obligatoires sont couvertes.")
        justification = (
            f"{c.title} {c.seniority.label} avec {c.years_experience:g} ans d'expérience ; "
            f"couvre {len([s for s in n.required_skills if s in cov.matched])}/"
            f"{len(n.required_skills)} compétences obligatoires.{missing_txt}"
        )
        if filters.warnings:
            justification += f" Point d'attention : {filters.warnings[0].lower()}."
        return JudgeVerdict(
            score=score,
            recommendation=_recommendation(score),
            strengths=strengths[:4] or ["Profil partiellement aligné"],
            gaps=gaps[:4],
            justification=justification,
        )


class LLMJudge:
    name = "llm"

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.fallback = HeuristicJudge()

    def evaluate(self, c, n, cov, sen_fit, filters) -> JudgeVerdict:
        profile = c.model_dump(
            mode="json",
            include={"title", "seniority", "years_experience", "skills", "experiences",
                     "certifications", "languages", "availability_date", "summary"},
        )
        need = n.model_dump(mode="json", exclude={"need_id", "client"})
        user = (
            f"BESOIN :\n{json.dumps(need, ensure_ascii=False)}\n\n"
            f"PROFIL :\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"Analyse automatique (indicative) : compétences couvertes {cov.matched}, "
            f"manquantes {cov.missing}, proches {cov.partial}.\n"
            f"Points d'attention des filtres : {filters.warnings}"
        )
        try:
            verdict = self.llm.complete_json(JUDGE_SYSTEM, user, JudgeVerdict)
        except LLMError as exc:
            log.error("Juge LLM indisponible, repli heuristique : %s", exc)
            return self.fallback.evaluate(c, n, cov, sen_fit, filters)
        # Cohérence score / recommandation imposée côté code
        verdict.recommendation = _recommendation(verdict.score)
        return verdict


def build_judge(llm: LLMClient | None) -> Judge:
    return LLMJudge(llm) if llm else HeuristicJudge()
