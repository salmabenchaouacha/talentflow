from datetime import date

import pytest
from conftest import TODAY

from talentflow.matching.engine import MatchingEngine
from talentflow.matching.filters import apply_filters
from talentflow.matching.judge import HeuristicJudge, LLMJudge
from talentflow.matching.scoring import seniority_fit, skill_coverage
from talentflow.schemas import CandidateProfile, JobNeed, RemotePolicy, Seniority, Skill


def cand(cid="C1", skills=("Python", "Airflow", "dbt", "SQL"), years=6.0, city="Toulouse",
         mobility=(), rate=500, avail=TODAY, langs=("français", "anglais"),
         sen=Seniority.SENIOR) -> CandidateProfile:
    return CandidateProfile(
        candidate_id=cid, title="Data Engineer", seniority=sen, years_experience=years,
        skills=[Skill(name=s, years=4) for s in skills], location=city, mobility=list(mobility),
        daily_rate=rate, availability_date=avail, languages=list(langs),
    )


@pytest.fixture
def need() -> JobNeed:
    return JobNeed(need_id="N1", title="Data Engineer senior",
                   required_skills=["Python", "Airflow", "dbt", "SQL"],
                   nice_to_have=["Snowflake"], min_years=5, seniority=Seniority.SENIOR,
                   location="Toulouse", remote_policy=RemotePolicy.HYBRID,
                   start_date=date(2026, 12, 1), max_daily_rate=550, languages=["anglais"])


# ------------------------------------------------------------------ filtres
def test_ideal_candidate_passes(need):
    assert apply_filters(cand(), need, TODAY).passed


@pytest.mark.parametrize("kwargs, reason", [
    ({"rate": 700}, "TJM"),
    ({"city": "Lille"}, "Localisation"),
    ({"avail": date(2027, 4, 1)}, "Disponible"),
    ({"langs": ("français",)}, "Langue"),
    ({"years": 2.0, "sen": Seniority.JUNIOR}, "expérience"),
])
def test_blocking_filters(need, kwargs, reason):
    res = apply_filters(cand(**kwargs), need, TODAY)
    assert not res.passed
    assert any(reason in r for r in res.reasons)


def test_soft_warnings(need):
    res = apply_filters(cand(rate=580, avail=date(2026, 12, 10)), need, TODAY)
    assert res.passed
    assert len(res.warnings) == 2


def test_remote_mobility_accepted_for_hybrid(need):
    res = apply_filters(cand(city="Lyon", mobility=["full remote"]), need, TODAY)
    assert res.passed and res.warnings


def test_full_remote_need_ignores_location(need):
    need.remote_policy = RemotePolicy.REMOTE
    assert apply_filters(cand(city="Lille"), need, TODAY).passed


# ------------------------------------------------------------------- scores
def test_coverage_with_partial_credit(need):
    full = skill_coverage(cand(skills=("Python", "Airflow", "dbt", "SQL", "Snowflake")), need)
    assert full.score == 1.0 and not full.missing
    partial = skill_coverage(cand(skills=("Python", "Airflow", "SQL", "BigQuery")), need)
    assert "dbt" in partial.missing
    assert ("Snowflake", "BigQuery") in partial.partial
    assert 0.6 < partial.score < 1.0


def test_seniority_fit(need):
    assert seniority_fit(cand(sen=Seniority.SENIOR), need) == 1.0
    assert seniority_fit(cand(sen=Seniority.CONFIRMED), need) == 0.5
    assert seniority_fit(cand(sen=Seniority.JUNIOR), need) == 0.0


# ------------------------------------------------------------------- moteur
def test_engine_ranks_and_excludes(need):
    retrieved = [
        (cand("BEST", skills=("Python", "Airflow", "dbt", "SQL", "Snowflake")), 0.70),
        (cand("OK", skills=("Python", "Airflow", "SQL")), 0.75),
        (cand("EXPENSIVE", rate=900), 0.80),
    ]
    engine = MatchingEngine(HeuristicJudge(), proposal_top_k=2, today=TODAY)
    report = engine.match(need, retrieved)
    assert [r.candidate_id for r in report.ranked] == ["BEST", "OK"]
    assert report.excluded[0].candidate_id == "EXPENSIVE"
    assert report.proposals[0].judge.recommendation == "proposer"
    assert report.ranked[0].final_score > report.ranked[1].final_score


def test_vector_mode_keeps_similarity_order(need):
    retrieved = [(cand("A"), 0.5), (cand("B", rate=900), 0.9)]
    report = MatchingEngine(HeuristicJudge(), today=TODAY).match(need, retrieved, mode="vector")
    assert [r.candidate_id for r in report.ranked] == ["B", "A"]


def test_llm_judge_enforces_consistent_recommendation(need, fake_llm):
    llm = fake_llm([{"score": 82, "recommendation": "ecarter", "strengths": ["Airflow"],
                     "gaps": [], "justification": "Très bon profil."}])
    c = cand()
    cov = skill_coverage(c, need)
    verdict = LLMJudge(llm).evaluate(c, need, cov, 1.0, apply_filters(c, need, TODAY))
    assert verdict.score == 82 and verdict.recommendation == "proposer"
    # le juge ne reçoit ni identifiant ni nom de client
    assert "N1" not in llm.prompts[0] and "candidate_id" not in llm.prompts[0]
