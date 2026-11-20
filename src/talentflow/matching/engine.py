"""Moteur de matching en 3 étages (entonnoir) :

1. Récupération : top-K profils par similarité vectorielle (PGVector / numpy).
2. Filtres métier + pré-score explicable (couverture, séniorité, profondeur, similarité).
3. LLM-as-a-judge sur les N meilleurs → score final = (1-w)·pré-score + w·juge.

Le mode `mode` permet l'étude d'ablation dans l'évaluation :
"vector" (étage 1 seul), "rules" (étages 1-2), "full" (1-2-3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from talentflow.matching.filters import apply_filters
from talentflow.matching.judge import Judge
from talentflow.matching.scoring import pre_score, seniority_fit, skill_coverage
from talentflow.schemas import CandidateProfile, JobNeed, MatchResult

Mode = Literal["vector", "rules", "full"]


@dataclass
class MatchReport:
    need_id: str
    proposals: list[MatchResult] = field(default_factory=list)   # top-k à valider
    ranked: list[MatchResult] = field(default_factory=list)      # tous les éligibles classés
    excluded: list[MatchResult] = field(default_factory=list)    # exclus + motifs
    retrieved: int = 0


class MatchingEngine:
    def __init__(self, judge: Judge, judge_top_n: int = 8, proposal_top_k: int = 3,
                 judge_weight: float = 0.4, rate_tolerance: float = 0.10,
                 availability_tolerance_days: int = 30, today: date | None = None) -> None:
        self.judge = judge
        self.judge_top_n = judge_top_n
        self.proposal_top_k = proposal_top_k
        self.judge_weight = judge_weight
        self.rate_tolerance = rate_tolerance
        self.availability_tolerance_days = availability_tolerance_days
        self.today = today or date.today()

    def match(self, need: JobNeed, retrieved: list[tuple[CandidateProfile, float]],
              mode: Mode = "full") -> MatchReport:
        report = MatchReport(need_id=need.need_id, retrieved=len(retrieved))

        if mode == "vector":
            ordered = sorted(retrieved, key=lambda t: -t[1])
            for rank, (c, vs) in enumerate(ordered, 1):
                cov = skill_coverage(c, need)
                from talentflow.schemas import FilterResult

                report.ranked.append(MatchResult(
                    need_id=need.need_id, candidate_id=c.candidate_id, rank=rank,
                    final_score=round(vs, 4), pre_score=round(vs, 4), vector_score=round(vs, 4),
                    skill_coverage=cov.score, matched_skills=cov.matched,
                    missing_skills=cov.missing, filters=FilterResult(passed=True),
                ))
            report.proposals = report.ranked[: self.proposal_top_k]
            return report

        scored: list[tuple[MatchResult, CandidateProfile]] = []
        for c, vs in retrieved:
            filters = apply_filters(c, need, today=self.today,
                                    rate_tolerance=self.rate_tolerance,
                                    availability_tolerance_days=self.availability_tolerance_days)
            cov = skill_coverage(c, need)
            sen = seniority_fit(c, need)
            ps = pre_score(cov.score, vs, sen, cov.depth)
            result = MatchResult(
                need_id=need.need_id, candidate_id=c.candidate_id,
                final_score=ps, pre_score=ps, vector_score=round(vs, 4),
                skill_coverage=cov.score, seniority_fit=round(sen, 3),
                matched_skills=cov.matched, missing_skills=cov.missing, filters=filters,
            )
            if filters.passed:
                scored.append((result, c))
            else:
                report.excluded.append(result)

        scored.sort(key=lambda t: -t[0].pre_score)

        if mode == "full":
            for result, c in scored[: self.judge_top_n]:
                cov = skill_coverage(c, need)
                verdict = self.judge.evaluate(c, need, cov, result.seniority_fit, result.filters)
                result.judge = verdict
                result.final_score = round(
                    (1 - self.judge_weight) * result.pre_score
                    + self.judge_weight * verdict.score / 100, 4
                )
            # Les profils non jugés restent classés derrière, avec leur pré-score pénalisé
            for result, _ in scored[self.judge_top_n :]:
                result.final_score = round(result.pre_score * (1 - self.judge_weight), 4)
            scored.sort(key=lambda t: -t[0].final_score)

        for rank, (result, _) in enumerate(scored, 1):
            result.rank = rank
            report.ranked.append(result)

        report.proposals = [
            r for r in report.ranked[: self.proposal_top_k]
            if r.judge is None or r.judge.recommendation != "ecarter"
        ]
        report.excluded.sort(key=lambda r: -r.pre_score)
        return report
