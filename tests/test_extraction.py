from datetime import date

import pytest
from conftest import CV_TEXT, NEED_TEXT, TODAY

from talentflow.extraction.extractor import LLMExtractor, RuleBasedExtractor, parse_availability
from talentflow.extraction.skills import detect_skills, normalize_skill, relatedness
from talentflow.llm.clients import LLMError, extract_json
from talentflow.privacy.pseudonymizer import Pseudonymizer
from talentflow.schemas import RemotePolicy, Seniority


# ----------------------------------------------------------------- skills
def test_normalize_aliases():
    assert normalize_skill("reactjs") == "React"
    assert normalize_skill("PySpark") == "Spark"
    assert normalize_skill("k8s") == "Kubernetes"
    assert normalize_skill("Outil maison") == "Outil maison"


def test_detect_does_not_confuse_java_and_javascript():
    assert detect_skills("Front en JavaScript et TypeScript") == ["JavaScript", "TypeScript"]
    assert "Java" in detect_skills("Back-end Java 17 et Spring Boot")


def test_relatedness():
    assert relatedness("PostgreSQL", "MySQL") == 0.5
    assert relatedness("Java", "Spring Boot") == 0.25
    assert relatedness("Python", "Excel") == 0.0


# --------------------------------------------------------------- CV rules
@pytest.fixture
def profile():
    text = Pseudonymizer().pseudonymize(CV_TEXT).text
    return RuleBasedExtractor(today=TODAY).extract_cv(text, "C1")


def test_cv_fields(profile):
    assert profile.title == "Data Engineer Senior"
    assert profile.seniority == Seniority.SENIOR
    assert profile.location == "Toulouse"
    assert profile.daily_rate == 520
    assert profile.availability_date == date(2026, 12, 1)
    assert profile.languages == ["français", "anglais"]
    assert "full remote" in profile.mobility


def test_cv_experiences_and_skill_years(profile):
    assert len(profile.experiences) == 2
    assert profile.experiences[0].company == "Aeronov"
    assert profile.experiences[0].end == "present"
    assert 9 <= profile.years_experience <= 10.5
    assert profile.skill_years("Airflow") == pytest.approx(6.0)
    assert {"Python", "dbt", "Snowflake", "Power BI", "ETL"} <= profile.skill_names


def test_availability_parsing():
    assert parse_availability("immédiate", TODAY) == TODAY
    assert parse_availability("dans 2 mois", TODAY) == date(2027, 1, 20)
    assert parse_availability("mars 2027", TODAY) == date(2027, 3, 1)
    assert parse_availability("15/01/2027", TODAY) == date(2027, 1, 15)


# -------------------------------------------------------------- need rules
def test_need_fields():
    need = RuleBasedExtractor(today=TODAY).extract_need(NEED_TEXT, "N1")
    assert need.client == "Aeronov" and need.sector == "aéronautique"
    assert need.required_skills == ["Python", "Airflow", "dbt", "SQL"]
    assert need.nice_to_have == ["Snowflake", "Kafka"]
    assert need.min_years == 5 and need.seniority == Seniority.SENIOR
    assert need.location == "Toulouse" and need.remote_policy == RemotePolicy.HYBRID
    assert need.start_date == date(2026, 12, 15) and need.duration_months == 6
    assert need.max_daily_rate == 550 and need.languages == ["anglais"]


def test_need_from_free_email_detects_skills():
    text = "Bonjour, nous cherchons un dev Java Spring Boot avec Kafka, démarrage ASAP à Toulouse."
    need = RuleBasedExtractor(today=TODAY).extract_need(text)
    assert {"Java", "Spring Boot", "Kafka"} <= set(need.required_skills)


# --------------------------------------------------------------------- LLM
def test_extract_json_tolerates_fences_and_text():
    assert extract_json('Voici :\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    with pytest.raises(LLMError):
        extract_json("pas de json")


VALID_NEED = {"title": "Data Engineer", "required_skills": ["python", "airflow"],
              "min_years": 4, "seniority": "confirme", "remote_policy": "hybride"}


def test_llm_extractor_retries_on_invalid_output(fake_llm):
    llm = fake_llm(['{"title": "x", "min_years": "beaucoup"}', VALID_NEED])
    need = LLMExtractor(llm, today=TODAY).extract_need("...", "N9")
    assert need.need_id == "N9"
    assert need.required_skills == ["Python", "Airflow"]  # normalisées par le schéma
    assert llm.usage.validation_retries == 1
    assert "invalide" in llm.prompts[1]


def test_llm_extractor_falls_back_to_rules(fake_llm):
    llm = fake_llm(["oops"] * 3)
    ext = LLMExtractor(llm, today=TODAY)
    need = ext.extract_need(NEED_TEXT)
    assert ext.fallback_count == 1
    assert need.max_daily_rate == 550


def test_access_error_trips_circuit_breaker(monkeypatch):
    import io
    import urllib.error

    from talentflow.llm.clients import OpenAICompatibleClient

    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.headers)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"error code: 1010"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    llm = OpenAICompatibleClient(base_url="https://example.test/v1", api_key="k", model="m")
    ext = LLMExtractor(llm, today=TODAY)
    for _ in range(5):
        ext.extract_need(NEED_TEXT)
    assert len(calls) == 1                        # un seul appel réseau, puis coupe-circuit
    assert "Talentflow" in calls[0]["User-agent"] or "TalentFlow" in calls[0]["User-agent"]
    assert ext.fallback_count == 5
