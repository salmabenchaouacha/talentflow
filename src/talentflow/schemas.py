"""Schémas Pydantic : contrat de données entre le LLM, le matching et le stockage.

Toute sortie du LLM est validée contre ces schémas avant d'être utilisée.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from talentflow.extraction.skills import normalize_skill


class Seniority(str, Enum):
    JUNIOR = "junior"
    CONFIRMED = "confirme"
    SENIOR = "senior"
    EXPERT = "expert"

    @property
    def label(self) -> str:
        return {"junior": "junior", "confirme": "confirmé", "senior": "senior",
                "expert": "expert"}[self.value]

    @property
    def rank(self) -> int:
        return {"junior": 0, "confirme": 1, "senior": 2, "expert": 3}[self.value]

    @classmethod
    def from_years(cls, years: float) -> Seniority:
        if years < 3:
            return cls.JUNIOR
        if years < 6:
            return cls.CONFIRMED
        if years < 10:
            return cls.SENIOR
        return cls.EXPERT

    @classmethod
    def parse(cls, value: str | None) -> Seniority | None:
        if not value:
            return None
        v = value.strip().lower()
        mapping = {
            "junior": cls.JUNIOR, "débutant": cls.JUNIOR, "debutant": cls.JUNIOR,
            "confirmé": cls.CONFIRMED, "confirme": cls.CONFIRMED, "intermédiaire": cls.CONFIRMED,
            "senior": cls.SENIOR, "sénior": cls.SENIOR,
            "expert": cls.EXPERT, "lead": cls.EXPERT, "architecte": cls.EXPERT,
        }
        for key, sen in mapping.items():
            if key in v:
                return sen
        return None


class RemotePolicy(str, Enum):
    ONSITE = "sur_site"
    HYBRID = "hybride"
    REMOTE = "full_remote"


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Skill(_Base):
    name: str = Field(description="Nom canonique de la compétence")
    years: float | None = Field(default=None, ge=0, le=45)
    category: str | None = None

    @field_validator("name")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return normalize_skill(v)


class Experience(_Base):
    title: str
    company: str | None = None
    start: str | None = None  # "2021-03" ou "2021"
    end: str | None = None    # "2023-06" ou "present"
    duration_months: int | None = Field(default=None, ge=0)
    skills: list[str] = Field(default_factory=list)
    summary: str | None = None

    @field_validator("skills")
    @classmethod
    def _norm_skills(cls, v: list[str]) -> list[str]:
        return sorted({normalize_skill(s) for s in v if s})


class CandidateProfile(_Base):
    """Profil consultant structuré, extrait d'un CV pseudonymisé."""

    candidate_id: str = ""
    title: str = Field(description="Intitulé du poste / métier")
    seniority: Seniority
    years_experience: float = Field(ge=0, le=50)
    skills: list[Skill] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    location: str | None = None
    mobility: list[str] = Field(default_factory=list)
    availability_date: date | None = None
    daily_rate: float | None = Field(default=None, ge=0, description="TJM en euros")
    summary: str = ""

    @field_validator("languages", "mobility")
    @classmethod
    def _lower_list(cls, v: list[str]) -> list[str]:
        return [x.strip().lower() for x in v if x and x.strip()]

    @property
    def skill_names(self) -> set[str]:
        return {s.name for s in self.skills}

    def skill_years(self, name: str) -> float | None:
        for s in self.skills:
            if s.name == name:
                return s.years
        return None

    def embedding_text(self) -> str:
        skills = ", ".join(
            f"{s.name} ({s.years:g} ans)" if s.years else s.name for s in self.skills
        )
        exps = " | ".join(
            f"{e.title}: {', '.join(e.skills)}. {e.summary or ''}" for e in self.experiences[:6]
        )
        return (
            f"{self.title}. Séniorité {self.seniority.value}, {self.years_experience:g} ans. "
            f"Compétences : {skills}. Expériences : {exps}. {self.summary}"
        )


class JobNeed(_Base):
    """Fiche de besoin client structurée."""

    need_id: str = ""
    client: str | None = None
    title: str
    summary: str = ""
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    min_years: float = Field(default=0, ge=0, le=40)
    seniority: Seniority | None = None
    location: str | None = None
    remote_policy: RemotePolicy = RemotePolicy.HYBRID
    start_date: date | None = None
    duration_months: int | None = Field(default=None, ge=0)
    max_daily_rate: float | None = Field(default=None, ge=0)
    languages: list[str] = Field(default_factory=list)
    sector: str | None = None

    @field_validator("required_skills", "nice_to_have")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for s in v:
            n = normalize_skill(s)
            if n and n not in seen:
                seen.append(n)
        return seen

    @field_validator("languages")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return [x.strip().lower() for x in v if x and x.strip()]

    def embedding_text(self) -> str:
        return (
            f"{self.title}. Compétences requises : {', '.join(self.required_skills)}. "
            f"Souhaitées : {', '.join(self.nice_to_have)}. "
            f"{self.min_years:g} ans minimum. {self.summary}"
        )


class FilterResult(_Base):
    passed: bool
    reasons: list[str] = Field(default_factory=list)   # motifs d'exclusion
    warnings: list[str] = Field(default_factory=list)  # points d'attention non bloquants


class JudgeVerdict(_Base):
    """Sortie du LLM-as-a-judge : note + justification lisible par un commercial."""

    score: int = Field(ge=0, le=100)
    recommendation: Literal["proposer", "a_etudier", "ecarter"]
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    justification: str


class MatchStatus(str, Enum):
    PENDING = "pending_review"
    VALIDATED = "validated"
    REJECTED = "rejected"
    SENT = "sent"


class MatchResult(_Base):
    need_id: str
    candidate_id: str
    rank: int = 0
    final_score: float = 0.0
    pre_score: float = 0.0
    vector_score: float = 0.0
    skill_coverage: float = 0.0
    seniority_fit: float = 0.0
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    filters: FilterResult
    judge: JudgeVerdict | None = None
    status: MatchStatus = MatchStatus.PENDING


class ProspectionEmail(_Base):
    prospect_company: str
    contact_role: str = ""
    subject: str
    body: str
    highlighted_profiles: list[str] = Field(default_factory=list)
    status: Literal["draft", "approved", "sent", "rejected"] = "draft"


class AuditEvent(_Base):
    ts: datetime = Field(default_factory=datetime.utcnow)
    actor: str
    action: str
    entity: str
    entity_id: str
    details: dict = Field(default_factory=dict)
