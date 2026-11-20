"""Campagnes de prospection personnalisées.

Pour chaque prospect : on part de ses signaux de recrutement (offres publiées, secteur),
on sélectionne 1 à 3 consultants disponibles qui y répondent, et on rédige un email
personnalisé (LLM, ou modèle Jinja hors-ligne). Les emails sont créés en brouillon :
un humain les approuve, puis n8n les envoie (workflow 03).

Les profils sont toujours présentés de façon anonyme (métier, séniorité, compétences).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2 import Template

from talentflow.generation.market import JobPost
from talentflow.llm.clients import LLMClient, LLMError
from talentflow.llm.prompts import PROSPECTION_SYSTEM
from talentflow.schemas import CandidateProfile, ProspectionEmail

log = logging.getLogger(__name__)


@dataclass
class Prospect:
    company: str
    sector: str
    city: str
    contact_role: str
    opt_out: bool = False


def load_prospects(path: str | Path) -> list[Prospect]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        return [
            Prospect(company=r["company"], sector=r["sector"], city=r["city"],
                     contact_role=r["contact_role"],
                     opt_out=r.get("opt_out", "").lower() in {"1", "true", "oui"})
            for r in csv.DictReader(f)
        ]


TEMPLATE = Template("""Bonjour,

{% if signal_title %}Nous avons vu que {{ company }} recherche actuellement des profils {{ signal_title }}{% if skills %} ({{ skills|join(', ') }}){% endif %}.{% else %}Les équipes {{ sector }} de la région de {{ city }} renforcent en ce moment leurs compétences en {{ skills|join(', ') }}.{% endif %}

Nous avons {{ profiles|length }} consultant{{ 's' if profiles|length > 1 else '' }} disponible{{ 's' if profiles|length > 1 else '' }} qui {{ 'correspondent' if profiles|length > 1 else 'correspond' }} à ce contexte :
{% for p in profiles %}- {{ p }}
{% endfor %}
Si le sujet est d'actualité, je vous propose un échange de 15 minutes cette semaine pour comprendre vos priorités et vous transmettre les dossiers de compétences.

Bien cordialement,
{{ sender }}

—
Vous ne souhaitez plus recevoir nos messages ? Répondez « STOP » à cet email.""")


def describe_profile(c: CandidateProfile, focus: list[str]) -> str:
    """Description anonyme : aucun nom, uniquement métier + séniorité + compétences."""
    key = [s for s in focus if s in c.skill_names][:3] or [s.name for s in c.skills[:3]]
    avail = ("disponible immédiatement" if not c.availability_date or c.availability_date <= date.today()
             else f"disponible le {c.availability_date:%d/%m/%Y}")
    return (f"{c.title} {c.seniority.label}, {c.years_experience:g} ans d'expérience "
            f"({', '.join(key)}), {avail}")


def select_profiles(candidates: list[CandidateProfile], skills: list[str], city: str,
                    max_profiles: int = 3) -> list[CandidateProfile]:
    def fits(c: CandidateProfile) -> bool:
        loc_ok = (not city or (c.location or "").lower() == city.lower()
                  or city.lower() in c.mobility or "full remote" in c.mobility
                  or "france" in c.mobility)
        return loc_ok and bool(set(skills) & c.skill_names)

    scored = sorted(
        (c for c in candidates if fits(c)),
        key=lambda c: (-len(set(skills) & c.skill_names), -c.years_experience),
    )
    return scored[:max_profiles]


class ProspectionWriter:
    def __init__(self, llm: LLMClient | None = None, sender: str = "L'équipe commerciale") -> None:
        self.llm = llm
        self.sender = sender

    def write(self, prospect: Prospect, posts: list[JobPost],
              candidates: list[CandidateProfile]) -> ProspectionEmail | None:
        if prospect.opt_out:
            return None
        own = [p for p in posts if p.company == prospect.company]
        skills: list[str] = []
        for p in own or [p for p in posts if p.sector == prospect.sector]:
            for s in p.skills:
                if s not in skills:
                    skills.append(s)
        skills = skills[:5]
        if not skills:
            return None
        chosen = select_profiles(candidates, skills, prospect.city)
        if not chosen:
            return None
        profiles = [describe_profile(c, skills) for c in chosen]
        signal_title = own[0].title if own else None

        if self.llm:
            try:
                return self._write_llm(prospect, skills, profiles, signal_title, chosen)
            except LLMError as exc:
                log.error("Rédaction LLM échouée, modèle hors-ligne utilisé : %s", exc)

        body = TEMPLATE.render(company=prospect.company, sector=prospect.sector,
                               city=prospect.city, skills=skills[:3], profiles=profiles,
                               signal_title=signal_title, sender=self.sender)
        subject = (f"{signal_title} : {len(profiles)} profil(s) disponible(s)" if signal_title
                   else f"Renfort {', '.join(skills[:2])} pour {prospect.company}")
        return ProspectionEmail(
            prospect_company=prospect.company, contact_role=prospect.contact_role,
            subject=subject, body=body, highlighted_profiles=[c.candidate_id for c in chosen],
        )

    def _write_llm(self, prospect, skills, profiles, signal_title, chosen) -> ProspectionEmail:
        from pydantic import BaseModel

        class _Out(BaseModel):
            subject: str
            body: str

        user = (
            f"Prospect : {prospect.company} (secteur {prospect.sector}, {prospect.city}), "
            f"destinataire : {prospect.contact_role}.\n"
            f"Signal observé : {signal_title or 'besoins du secteur'} ; compétences : {skills}.\n"
            f"Profils disponibles (anonymes) :\n- " + "\n- ".join(profiles) +
            f"\nSignature : {self.sender}"
        )
        out = self.llm.complete_json(PROSPECTION_SYSTEM, user, _Out)
        body = out.body
        if "stop" not in body.lower():
            body += "\n\n—\nVous ne souhaitez plus recevoir nos messages ? Répondez « STOP »."
        return ProspectionEmail(
            prospect_company=prospect.company, contact_role=prospect.contact_role,
            subject=out.subject, body=body,
            highlighted_profiles=[c.candidate_id for c in chosen],
        )
