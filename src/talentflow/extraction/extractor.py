"""Extraction structurée des CV et des fiches de besoin.

Deux implémentations derrière la même interface :
- `LLMExtractor` : LLM + schéma Pydantic + boucle de correction (production) ;
- `RuleBasedExtractor` : règles + référentiel de compétences, hors-ligne et déterministe.
  Il sert de repli si le LLM échoue, de base de comparaison dans l'évaluation,
  et permet de faire tourner la démo et la CI sans clé d'API.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from talentflow.extraction.skills import detect_skills, normalize_skill
from talentflow.llm.clients import LLMClient, LLMError
from talentflow.llm.prompts import CV_EXTRACTION_SYSTEM, NEED_EXTRACTION_SYSTEM, PROMPT_VERSION
from talentflow.schemas import (
    CandidateProfile,
    Experience,
    JobNeed,
    RemotePolicy,
    Seniority,
    Skill,
)

log = logging.getLogger(__name__)

MAX_CHARS = 14_000

_MONTHS_FR = {
    "janvier": 1, "janv": 1, "jan": 1, "février": 2, "fevrier": 2, "févr": 2, "fev": 2,
    "fév": 2, "mars": 3, "mar": 3, "avril": 4, "avr": 4, "mai": 5, "juin": 6, "juillet": 7,
    "juil": 7, "août": 8, "aout": 8, "septembre": 9, "sept": 9, "sep": 9, "octobre": 10,
    "oct": 10, "novembre": 11, "nov": 11, "décembre": 12, "decembre": 12, "déc": 12, "dec": 12,
}
_LANGS = ["français", "anglais", "espagnol", "allemand", "italien", "arabe", "portugais",
          "néerlandais", "chinois", "japonais", "russe"]
_SECTION = re.compile(
    r"^\s*#*\s*(profil|résumé|compétences(?: techniques)?|expériences?(?: professionnelles?)?|"
    r"formations?|certifications?|langues|centres d'intérêt|projets)\s*:?\s*$",
    re.IGNORECASE,
)
_DATE_TOKEN = r"(?:\d{1,2}/\d{4}|\d{4}-\d{2}|[A-Za-zéûè]{3,9}\.?\s\d{4}|\d{4})"
_RANGE = re.compile(
    rf"(?P<s>{_DATE_TOKEN})\s*(?:–|-|—|à|au)\s*"
    rf"(?P<e>{_DATE_TOKEN}|présent|aujourd['’]hui|actuel(?:lement)?|en cours|now|present)",
    re.IGNORECASE,
)


class Extractor(Protocol):
    name: str

    def extract_cv(self, text: str, candidate_id: str = "") -> CandidateProfile: ...

    def extract_need(self, text: str, need_id: str = "") -> JobNeed: ...


# ====================================================================== helpers
def _parse_date_token(token: str, today: date, end: bool = False) -> date | None:
    t = token.strip().lower().rstrip(".")
    if re.match(r"présent|aujourd|actuel|en cours|now|present", t):
        return today
    m = re.match(r"(\d{1,2})/(\d{4})$", t)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
    elif (m := re.match(r"(\d{4})-(\d{2})$", t)):
        year, month = int(m.group(1)), int(m.group(2))
    elif (m := re.match(r"([a-zéûè]+)\.?\s(\d{4})$", t)):
        month = _MONTHS_FR.get(m.group(1), 0)
        year = int(m.group(2))
        if not month:
            return None
    elif re.match(r"\d{4}$", t):
        year, month = int(t), (12 if end else 1)
    else:
        return None
    if not 1 <= month <= 12 or not 1970 <= year <= today.year + 1:
        return None
    day = calendar.monthrange(year, month)[1] if end else 1
    return date(year, month, day)


def _months_between(a: date, b: date) -> int:
    return max(0, (b.year - a.year) * 12 + (b.month - a.month) + 1)


def _add_months(d: date, n: int) -> date:
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def parse_availability(value: str, today: date) -> date | None:
    v = value.strip().lower()
    if re.search(r"imm[ée]diat|asap|de suite|tout de suite", v):
        return today
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", v)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(?:dans|préavis(?: de)?|sous)\s*(\d+)\s*mois", v)
    if m:
        return _add_months(today, int(m.group(1)))
    m = re.search(r"(?:dans|sous)\s*(\d+)\s*semaines?", v)
    if m:
        return today + timedelta(weeks=int(m.group(1)))
    m = re.search(r"([a-zéûè]+)\s(\d{4})", v)
    if m and m.group(1) in _MONTHS_FR:
        return date(int(m.group(2)), _MONTHS_FR[m.group(1)], 1)
    return None


def _field(text: str, *labels: str) -> str | None:
    for label in labels:
        m = re.search(rf"(?:^|\||\n|—)\s*{label}\s*:\s*([^\n|]+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _money(value: str | None) -> float | None:
    if not value:
        return None
    m = re.search(r"(\d[\d\s]{1,6})", value)
    return float(m.group(1).replace(" ", "")) if m else None


def _sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_header": []}
    current = "_header"
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            current = m.group(1).lower().split()[0].rstrip("s")
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _split_list(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;/•]|\bet\b", value) if x.strip()]


# ================================================================ rule-based
class RuleBasedExtractor:
    name = "rules"

    def __init__(self, today: date | None = None) -> None:
        self.today = today or date.today()

    # ------------------------------------------------------------------ CV
    def extract_cv(self, text: str, candidate_id: str = "") -> CandidateProfile:
        today = self.today
        secs = _sections(text)
        header_lines = [ln.strip() for ln in secs.get("_header", []) if ln.strip()]

        title = self._cv_title(header_lines)
        experiences = self._experiences(secs.get("expérience", []) or text.splitlines(), today)

        explicit_years = None
        m = re.search(r"(\d{1,2})\s*(?:\+\s*)?ans d['’]exp[ée]rience", text, re.IGNORECASE)
        if not m:
            m = re.search(r"ann[ée]es d['’]exp[ée]rience\s*:\s*(\d{1,2})", text, re.IGNORECASE)
        if m:
            explicit_years = float(m.group(1))
        computed_years = self._total_years(experiences, today)
        years = explicit_years if explicit_years is not None else computed_years

        seniority = Seniority.parse(title) or Seniority.from_years(years)

        # Compétences : détection dans tout le document, années calculées par expérience
        all_skills = detect_skills(text)
        skills = []
        for name in all_skills:
            months = sum(e.duration_months or 0 for e in experiences if name in e.skills)
            skills.append(Skill(name=name, years=round(months / 12 * 2) / 2 if months else None))

        location = _field(text, "localisation", "lieu", "ville", "basée? à")
        if location:
            location = location.split("(")[0].strip()
        mobility_raw = _field(text, "mobilité", "mobilite")
        mobility = _split_list(mobility_raw) if mobility_raw else []
        avail_raw = _field(text, "disponibilité", "disponibilite", "disponible")
        availability = parse_availability(avail_raw, today) if avail_raw else None
        rate = _money(_field(text, "tjm", "taux journalier"))

        lang_block = " ".join(secs.get("langue", [])) or (_field(text, "langues") or "")
        languages = [lg for lg in _LANGS if lg in lang_block.lower()]

        education = [ln.strip("-• ").strip() for ln in secs.get("formation", []) if ln.strip()]
        certifs = [ln.strip("-• ").strip() for ln in secs.get("certification", []) if ln.strip()]
        summary = " ".join(ln.strip() for ln in secs.get("profil", []) if ln.strip())[:400]

        return CandidateProfile(
            candidate_id=candidate_id,
            title=title,
            seniority=seniority,
            years_experience=round(years, 1),
            skills=skills,
            experiences=experiences,
            education=education[:5],
            certifications=certifs[:8],
            languages=languages,
            location=location,
            mobility=mobility,
            availability_date=availability,
            daily_rate=rate,
            summary=summary,
        )

    @staticmethod
    def _cv_title(header_lines: list[str]) -> str:
        for line in header_lines[:6]:
            clean = re.sub(r"\[[A-Z_]+_\d+\]", "", line).strip(" -|—#*")
            if not clean or ":" in clean or len(clean) > 70 or re.search(r"\d", clean):
                continue
            return clean
        return "Consultant"

    def _experiences(self, lines: list[str], today: date) -> list[Experience]:
        exps: list[Experience] = []
        cleaned = [ln.strip() for ln in lines]
        idx = [i for i, ln in enumerate(cleaned) if _RANGE.search(ln)]
        for n, i in enumerate(idx):
            m = _RANGE.search(cleaned[i])
            start = _parse_date_token(m.group("s"), today)
            end = _parse_date_token(m.group("e"), today, end=True)
            if not start or not end or end < start:
                continue
            # Intitulé : sur la ligne de dates (avant les dates) ou la ligne précédente
            head = cleaned[i][: m.start()].strip(" -|—:,")
            if not head and i > 0:
                head = cleaned[i - 1]
            title, company = self._split_title_company(head)
            stop = idx[n + 1] - 1 if n + 1 < len(idx) else len(cleaned)
            body = " ".join(cleaned[i + 1 : stop]).strip()
            skills = detect_skills(f"{head} {body}")
            exps.append(Experience(
                title=title or "Poste",
                company=company,
                start=start.strftime("%Y-%m"),
                end="present" if end == today else end.strftime("%Y-%m"),
                duration_months=_months_between(start, end),
                skills=skills,
                summary=body[:240] or None,
            ))
        return exps

    @staticmethod
    def _split_title_company(head: str) -> tuple[str, str | None]:
        for sep in (" — ", " – ", " - ", " chez ", " | ", " @ ", ", "):
            if sep in head:
                a, b = head.split(sep, 1)
                return a.strip(), b.split("(")[0].strip() or None
        return head.strip(), None

    @staticmethod
    def _total_years(experiences: list[Experience], today: date) -> float:
        """Somme des périodes en fusionnant les chevauchements."""
        spans = []
        for e in experiences:
            s = _parse_date_token(e.start or "", today)
            en = today if e.end == "present" else _parse_date_token(e.end or "", today, end=True)
            if s and en:
                spans.append((s, en))
        spans.sort()
        merged: list[list[date]] = []
        for s, e in spans:
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        months = sum(_months_between(s, e) for s, e in merged)
        return round(months / 12, 1)

    # ---------------------------------------------------------------- Need
    def extract_need(self, text: str, need_id: str = "") -> JobNeed:
        today = self.today
        title = _field(text, "intitulé", "intitule", "poste", "profil recherché", "objet")
        if not title:
            first = next((ln for ln in text.splitlines() if ln.strip()), "Besoin")
            title = first.strip("#* ")
        client = _field(text, "client", "société", "entreprise")
        sector = None
        if client and "(" in client:
            sector = client.split("(", 1)[1].rstrip(")").replace("secteur", "").strip()
            client = client.split("(", 1)[0].strip()

        req_raw = _field(text, "compétences obligatoires", "compétences requises",
                         "must have", "indispensables?", "stack")
        nice_raw = _field(text, "compétences souhaitées", "souhaitées?", "nice to have",
                          "un plus", "appréciées?")
        required = [normalize_skill(s) for s in _split_list(req_raw)] if req_raw else []
        nice = [normalize_skill(s) for s in _split_list(nice_raw)] if nice_raw else []
        if not required:  # email libre : on détecte tout ce qui est cité
            required = detect_skills(text)[:6]

        min_years = 0.0
        m = re.search(r"(\d{1,2})\s*(?:\+\s*)?ans", _field(text, "expérience", "experience") or "")
        if not m:
            m = re.search(r"(\d{1,2})\s*(?:\+\s*)?ans (?:d['’]expérience )?minimum", text, re.I)
        if m:
            min_years = float(m.group(1))

        seniority = Seniority.parse(_field(text, "séniorité", "seniorite", "niveau") or "") \
            or Seniority.parse(title)

        loc_raw = _field(text, "lieu", "localisation", "site") or ""
        location = re.split(r"[—\-(,]", loc_raw)[0].strip() or None
        remote_raw = (_field(text, "télétravail", "teletravail", "remote") or loc_raw).lower()
        if re.search(r"full remote|100\s?% (?:télé|remote)|totalement", remote_raw):
            remote = RemotePolicy.REMOTE
        elif re.search(r"aucun|sur site|présentiel|0 jour|non", remote_raw):
            remote = RemotePolicy.ONSITE
        else:
            remote = RemotePolicy.HYBRID

        start_raw = _field(text, "démarrage", "demarrage", "date de début", "début")
        start = parse_availability(start_raw, today) if start_raw else None
        dur = re.search(r"(\d{1,2})\s*mois", _field(text, "durée", "duree") or "")
        rate = _money(_field(text, "tjm max(?:imum)?", "tjm", "budget"))
        lang_raw = (_field(text, "langues?") or "").lower()
        languages = [lg for lg in _LANGS if lg in lang_raw]
        summary = _field(text, "contexte", "description", "mission") or ""

        return JobNeed(
            need_id=need_id,
            client=client,
            title=title,
            summary=summary[:400],
            required_skills=required,
            nice_to_have=[s for s in nice if s not in required],
            min_years=min_years,
            seniority=seniority,
            location=location,
            remote_policy=remote,
            start_date=start,
            duration_months=int(dur.group(1)) if dur else None,
            max_daily_rate=rate,
            languages=languages,
            sector=sector,
        )


# ====================================================================== LLM
class LLMExtractor:
    """Extraction par LLM, avec repli automatique sur les règles en cas d'échec.

    Cache disque optionnel (clé = modèle + version du prompt + texte pseudonymisé) :
    relancer l'évaluation ne rappelle pas le LLM pour un document déjà traité, ce qui
    économise les quotas. Le cache ne contient que des données pseudonymisées.
    """

    name = "llm"

    def __init__(self, llm: LLMClient, fallback: RuleBasedExtractor | None = None,
                 today: date | None = None, cache_path: str | Path | None = None) -> None:
        self.llm = llm
        self.today = today or date.today()
        self.fallback = fallback or RuleBasedExtractor(today=self.today)
        self.fallback_count = 0
        self.cache_hits = 0
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, dict] = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}

    def _key(self, kind: str, text: str) -> str:
        raw = f"{self.llm.model}|{PROMPT_VERSION}|{kind}|{self.today}|{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _remember(self, key: str, value: dict) -> None:
        if not self.cache_path:
            return
        self._cache[key] = value
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")

    def extract_cv(self, text: str, candidate_id: str = "") -> CandidateProfile:
        key = self._key("cv", text)
        if key in self._cache:
            self.cache_hits += 1
            profile = CandidateProfile.model_validate(self._cache[key])
            profile.candidate_id = candidate_id
            return profile
        user = f"Date du jour : {self.today.isoformat()}\n\nCV :\n\"\"\"\n{text[:MAX_CHARS]}\n\"\"\""
        try:
            profile = self.llm.complete_json(CV_EXTRACTION_SYSTEM, user, CandidateProfile)
        except LLMError as exc:
            log.error("Extraction LLM du CV échouée, repli sur les règles : %s", exc)
            self.fallback_count += 1
            return self.fallback.extract_cv(text, candidate_id)
        # Filet de sécurité : compétences citées dans le CV mais oubliées par le LLM
        known = profile.skill_names
        for name in detect_skills(text):
            if name not in known:
                profile.skills.append(Skill(name=name))
        profile.candidate_id = ""
        self._remember(key, profile.model_dump(mode="json"))
        profile.candidate_id = candidate_id
        return profile

    def extract_need(self, text: str, need_id: str = "") -> JobNeed:
        key = self._key("need", text)
        if key in self._cache:
            self.cache_hits += 1
            need = JobNeed.model_validate(self._cache[key])
            need.need_id = need_id
            return need
        user = f"Date du jour : {self.today.isoformat()}\n\nBesoin :\n\"\"\"\n{text[:MAX_CHARS]}\n\"\"\""
        try:
            need = self.llm.complete_json(NEED_EXTRACTION_SYSTEM, user, JobNeed)
        except LLMError as exc:
            log.error("Extraction LLM du besoin échouée, repli sur les règles : %s", exc)
            self.fallback_count += 1
            return self.fallback.extract_need(text, need_id)
        need.need_id = ""
        self._remember(key, need.model_dump(mode="json"))
        need.need_id = need_id
        return need


def build_extractor(llm: LLMClient | None, today: date | None = None,
                    cache_path: str | Path | None = None) -> Extractor:
    rules = RuleBasedExtractor(today=today)
    return LLMExtractor(llm, fallback=rules, today=today, cache_path=cache_path) if llm else rules
