"""Génération de données 100 % synthétiques (aucun vrai CV : RGPD oblige).

Produit, de façon déterministe (graine fixe) :
- des CV de consultants fictifs (DOCX, TXT, MD) avec de fausses données personnelles,
  pour tester la pseudonymisation ;
- des fiches de besoin clients fictifs (TXT, MD au format email, DOCX) ;
- la « vérité terrain » de chaque profil (métier, compétences, contraintes) et des
  jugements de pertinence besoin × consultant (gold) pour l'évaluation ;
- des offres d'emploi publiques et des prospects fictifs pour la veille et la prospection ;
- un export au format BoondManager pour le faux serveur Boond.

Les jugements de pertinence sont dérivés des attributs latents (et non du texte des CV) :
ils testent donc l'extraction + le matching. Ils doivent être relus à la main
(data/eval/annotations_a_relire.csv) avant de citer un résultat.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ARCHETYPES: dict[str, dict] = {
    "data_engineer": {
        "titles": ["Data Engineer", "Ingénieur Data", "Data Engineer Cloud"],
        "core": ["Python", "SQL", "Airflow", "dbt", "Spark"],
        "opt": ["Snowflake", "BigQuery", "Docker", "Kafka", "AWS", "Databricks", "PostgreSQL"],
        "adjacent": ["data_scientist", "devops"],
        "rate": 480,
    },
    "data_scientist": {
        "titles": ["Data Scientist", "Data Scientist NLP", "Machine Learning Engineer"],
        "core": ["Python", "Machine Learning", "Scikit-learn", "Pandas", "SQL"],
        "opt": ["PyTorch", "NLP", "Deep Learning", "MLOps", "Spark", "Computer Vision"],
        "adjacent": ["data_engineer", "ai_engineer"],
        "rate": 500,
    },
    "ai_engineer": {
        "titles": ["Ingénieur IA", "AI Engineer", "Ingénieur IA générative"],
        "core": ["Python", "LLM", "RAG", "LangChain", "FastAPI"],
        "opt": ["LangGraph", "Vector Databases", "Docker", "Hugging Face", "Azure",
                "Prompt Engineering", "NLP"],
        "adjacent": ["data_scientist", "automation"],
        "rate": 560,
    },
    "java_backend": {
        "titles": ["Développeur Java", "Développeur Back-end Java", "Ingénieur Java Spring"],
        "core": ["Java", "Spring Boot", "REST API", "SQL", "Microservices"],
        "opt": ["Kafka", "Docker", "Kubernetes", "PostgreSQL", "Angular", "Hibernate", "Git"],
        "adjacent": ["dotnet"],
        "rate": 470,
    },
    "dotnet": {
        "titles": ["Développeur .NET", "Développeur C# .NET", "Ingénieur .NET Azure"],
        "core": ["C#", ".NET", "SQL Server", "REST API"],
        "opt": ["Azure", "Angular", "Docker", "Microservices", "Git", "Tests automatisés"],
        "adjacent": ["java_backend"],
        "rate": 460,
    },
    "frontend": {
        "titles": ["Développeur Front-end", "Développeur React", "Développeur Web Front"],
        "core": ["JavaScript", "TypeScript", "React", "HTML/CSS"],
        "opt": ["Node.js", "Vue.js", "Angular", "Tests automatisés", "Git", "GraphQL"],
        "adjacent": [],
        "rate": 430,
    },
    "devops": {
        "titles": ["Ingénieur DevOps", "DevOps Cloud", "Ingénieur SRE"],
        "core": ["Docker", "Kubernetes", "Terraform", "CI/CD", "Linux"],
        "opt": ["AWS", "Azure", "Ansible", "Prometheus", "Bash", "Python", "GCP"],
        "adjacent": ["data_engineer"],
        "rate": 540,
    },
    "automation": {
        "titles": ["Consultant Automatisation", "Ingénieur Automatisation & IA",
                   "Développeur Low-code"],
        "core": ["Python", "n8n", "Power Automate", "REST API", "Microsoft 365"],
        "opt": ["Make", "Zapier", "LLM", "Salesforce", "SQL", "Azure"],
        "adjacent": ["ai_engineer"],
        "rate": 450,
    },
    "qa": {
        "titles": ["Ingénieur QA", "Testeur automaticien", "QA Engineer"],
        "core": ["Selenium", "Tests automatisés", "Agile Scrum"],
        "opt": ["Java", "Python", "CI/CD", "JavaScript", "Git"],
        "adjacent": [],
        "rate": 400,
    },
    "pm": {
        "titles": ["Chef de projet IT", "Product Owner", "Chef de projet Data"],
        "core": ["Gestion de projet", "Agile Scrum"],
        "opt": ["SAP", "Power BI", "SQL", "Microsoft 365", "Salesforce"],
        "adjacent": [],
        "rate": 550,
    },
}
WEIGHTS = {"data_engineer": 9, "data_scientist": 7, "ai_engineer": 7, "java_backend": 9,
           "dotnet": 6, "frontend": 6, "devops": 6, "automation": 4, "qa": 3, "pm": 3}

FIRST = ["Camille", "Yasmine", "Thomas", "Inès", "Lucas", "Sarah", "Mehdi", "Léa", "Hugo",
         "Amira", "Nicolas", "Chloé", "Karim", "Manon", "Julien", "Nour", "Antoine", "Emma",
         "Rayan", "Clara", "Maxime", "Lina", "Romain", "Salomé", "Adam", "Pauline", "Yanis",
         "Margaux", "Bilel", "Océane"]
LAST = ["Martin", "Bernard", "Haddad", "Dubois", "Lefèvre", "Benali", "Moreau", "Laurent",
        "Garcia", "Mansouri", "Roux", "Fournier", "Girard", "Trabelsi", "Bonnet", "Mercier",
        "Chevalier", "Rahmani", "Blanc", "Faure", "Rousseau", "Gauthier", "Ziani", "Perrin"]
STREETS = ["rue des Lilas", "avenue de Grande-Bretagne", "rue Alsace-Lorraine",
           "boulevard Carnot", "allée Jean Jaurès", "rue du Taur", "chemin de la Garonne"]
CITIES = {"Toulouse": "31000", "Paris": "75011", "Lyon": "69003", "Bordeaux": "33000",
          "Nantes": "44000", "Montpellier": "34000"}
CITY_WEIGHTS = {"Toulouse": 50, "Paris": 18, "Lyon": 9, "Bordeaux": 9, "Nantes": 7,
                "Montpellier": 7}
COMPANIES = ["Aeronov", "BanqueSud", "Occitel", "MedAlpha", "Garonne Énergie",
             "Novasys Industrie", "AssurOuest", "LogiRail", "SkyPort Services", "Retailia",
             "AgriData Sud", "CityMobility", "Pyrénées Santé", "Capitole Finance"]
SECTORS = {"Aeronov": "aéronautique", "BanqueSud": "banque", "Occitel": "télécoms",
           "MedAlpha": "santé", "Garonne Énergie": "énergie", "Novasys Industrie": "industrie",
           "AssurOuest": "assurance", "LogiRail": "transport", "SkyPort Services": "aéronautique",
           "Retailia": "retail", "AgriData Sud": "agroalimentaire", "CityMobility": "mobilité",
           "Pyrénées Santé": "santé", "Capitole Finance": "banque"}
TASKS = {
    "data_engineer": ["Conception de pipelines d'ingestion batch et streaming",
                      "Modélisation des données et industrialisation des transformations",
                      "Migration d'un entrepôt de données vers le cloud"],
    "data_scientist": ["Modèles de prévision de la demande", "Scoring de churn client",
                       "Classification automatique de documents"],
    "ai_engineer": ["Assistant RAG sur la documentation interne",
                    "Agent IA de qualification de tickets",
                    "Extraction d'informations de contrats par LLM"],
    "java_backend": ["Développement d'API de gestion de contrats",
                     "Refonte d'un monolithe en microservices",
                     "Plateforme de paiement à fort trafic"],
    "dotnet": ["Application métier de gestion des sinistres", "API de facturation",
               "Portail client et back-office"],
    "frontend": ["Refonte de l'espace client", "Design system et composants réutilisables",
                 "Application web de suivi logistique"],
    "devops": ["Mise en place de la chaîne CI/CD", "Migration vers Kubernetes",
               "Infrastructure as code multi-environnements"],
    "automation": ["Automatisation des processus RH et achats",
                   "Intégration CRM / outils bureautiques par API",
                   "Workflows d'approbation et de reporting"],
    "qa": ["Automatisation des tests de non-régression", "Stratégie de tests et recette",
           "Tests de bout en bout dans la CI"],
    "pm": ["Pilotage d'un programme de transformation", "Coordination d'une équipe agile",
           "Cadrage et suivi budgétaire de projets"],
}


@dataclass
class Consultant:
    cid: str
    first: str
    last: str
    email: str
    phone: str
    address: str
    birth: str | None
    archetype: str
    title: str
    years: float
    skills: list[str]
    city: str
    mobility: list[str]
    availability: date
    rate: int
    languages: list[str]
    experiences: list[dict] = field(default_factory=list)
    fmt: str = "txt"

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"


@dataclass
class Need:
    nid: str
    client: str
    sector: str
    archetype: str
    title: str
    seniority: str
    min_years: int
    required: list[str]
    nice: list[str]
    city: str
    remote: str
    start: date
    duration: int
    max_rate: int
    languages: list[str]
    contact: str
    fmt: str = "txt"


def _seniority(years: float) -> str:
    return "junior" if years < 3 else "confirmé" if years < 6 else "senior" if years < 10 else "expert"


def _sen_rank(s: str) -> int:
    return {"junior": 0, "confirmé": 1, "senior": 2, "expert": 3}[s]


def _pick_city(rng: random.Random) -> str:
    return rng.choices(list(CITY_WEIGHTS), weights=list(CITY_WEIGHTS.values()))[0]


# ============================================================== consultants
def make_consultants(n: int, today: date, rng: random.Random,
                     planted_for: list[Need] | None = None) -> list[Consultant]:
    """n consultants aléatoires, plus 2 profils « plantés » par besoin (cas positifs)
    et 1 piège (bonnes compétences mais contrainte bloquante) pour un besoin sur deux."""
    out: list[Consultant] = []
    used: set[tuple[str, str]] = set()
    for _ in range(n):
        arch = rng.choices(list(WEIGHTS), weights=list(WEIGHTS.values()))[0]
        out.append(_new_consultant(len(out), arch, today, rng, used))
    for k, nd in enumerate(planted_for or []):
        for j in range(2):
            years = nd.min_years + rng.uniform(0.5, 3.5)
            if nd.seniority == "junior":
                years = rng.uniform(1.2, 2.8)
            out.append(_new_consultant(
                len(out), nd.archetype, today, rng, used,
                years=round(years, 1),
                skills=list(dict.fromkeys(nd.required + rng.sample(nd.nice, j + 1)
                                          + rng.sample(ARCHETYPES[nd.archetype]["opt"], 2))),
                city=nd.city,
                availability=min(nd.start, today + timedelta(days=rng.choice([0, 0, 10]))),
                rate=int(nd.max_rate * rng.uniform(0.85, 0.98) / 10) * 10,
                languages=["français", "anglais"],
            ))
        if k % 2 == 0:  # piège : profil idéal mais indisponible avant 5 mois
            out.append(_new_consultant(
                len(out), nd.archetype, today, rng, used,
                years=round(nd.min_years + 2.0, 1), skills=nd.required + nd.nice,
                city=nd.city, availability=today + timedelta(days=150),
                rate=int(nd.max_rate * 0.9 / 10) * 10, languages=["français", "anglais"],
            ))
    rng.shuffle(out)
    for i, c in enumerate(out):  # identifiants stables après mélange
        c.cid = f"GT-C{i + 1:03d}"
    return out


def _new_consultant(i: int, arch: str, today: date, rng: random.Random,
                    used: set[tuple[str, str]], **over) -> Consultant:
    spec = ARCHETYPES[arch]
    while True:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        if (first, last) not in used:
            used.add((first, last))
            break
    years = over.get("years", round(rng.uniform(1.0, 15.0), 1))
    if "skills" in over:
        skills = over["skills"]
    else:
        n_core = len(spec["core"]) if rng.random() < 0.6 else len(spec["core"]) - 1
        skills = rng.sample(spec["core"], n_core) + rng.sample(spec["opt"], rng.randint(2, 4))
        if rng.random() < 0.25:  # profil hybride
            other = ARCHETYPES[rng.choice(list(ARCHETYPES))]
            skills.append(rng.choice(other["core"]))
    skills = list(dict.fromkeys(skills))
    city = over.get("city", _pick_city(rng))
    mobility = [city]
    r = rng.random()
    if r < 0.25:
        mobility.append("Full remote")
    elif r < 0.40:
        mobility.append("France")
    elif r < 0.55:
        mobility.append(rng.choice([c for c in CITIES if c != city]))
    if "availability" in over:
        availability = over["availability"]
    else:
        avail_r = rng.random()
        if avail_r < 0.40:
            availability = today
        elif avail_r < 0.85:
            availability = today + timedelta(days=rng.choice([15, 30, 45, 60, 90]))
        else:
            availability = today + timedelta(days=rng.choice([120, 150, 180]))
    sen = _seniority(years)
    rate = over.get("rate") or int(
        spec["rate"] * {"junior": 0.75, "confirmé": 0.95, "senior": 1.15, "expert": 1.35}[sen]
        * rng.uniform(0.9, 1.12) / 10) * 10
    languages = over.get("languages") or (
        ["français"] + (["anglais"] if rng.random() < 0.75 else [])
        + (["espagnol"] if rng.random() < 0.15 else []))
    ascii_ = str.maketrans("éèëêïîç", "eeeeiic")
    c = Consultant(
        cid=f"GT-C{i + 1:03d}", first=first, last=last,
        email=f"{first.lower()}.{last.lower()}@mail-perso.fr".translate(ascii_),
        phone=f"06 {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)} "
              f"{rng.randint(10, 99)}",
        address=f"{rng.randint(1, 120)} {rng.choice(STREETS)}, {CITIES[city]} {city}",
        birth=(f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/"
               f"{today.year - 23 - int(years) - rng.randint(0, 3)}")
        if rng.random() < 0.35 else None,
        archetype=arch, title=rng.choice(spec["titles"]), years=years, skills=skills,
        city=city, mobility=mobility, availability=availability, rate=rate,
        languages=languages,
        fmt=rng.choices(["docx", "txt", "md"], weights=[5, 4, 1])[0],
    )
    c.experiences = _experiences(c, today, rng)
    return c


def _experiences(c: Consultant, today: date, rng: random.Random) -> list[dict]:
    months_left = int(c.years * 12)
    end = today
    exps = []
    n = 1 if months_left < 24 else rng.randint(2, 4)
    for k in range(n):
        dur = months_left if k == n - 1 else rng.randint(12, max(13, months_left - 12 * (n - k - 1)))
        dur = max(6, min(dur, months_left))
        start = date(end.year, end.month, 1)
        for _ in range(dur - 1):
            start = (start - timedelta(days=1)).replace(day=1)
        company = rng.choice(COMPANIES)
        exp_skills = rng.sample(c.skills, min(len(c.skills), rng.randint(3, 5)))
        exps.append({
            "title": c.title if k == 0 else rng.choice(ARCHETYPES[c.archetype]["titles"]),
            "company": company,
            "start": start, "end": None if k == 0 else end,
            "task": rng.choice(TASKS[c.archetype]),
            "skills": exp_skills,
        })
        months_left -= dur
        if months_left < 6:
            break
        end = (start - timedelta(days=1))
    return exps


def _fmt_month(d: date | None) -> str:
    return "Présent" if d is None else f"{d.month:02d}/{d.year}"


def consultant_text(c: Consultant, today: date) -> str:
    avail = ("immédiate" if c.availability <= today
             else c.availability.strftime("%d/%m/%Y"))
    lines = [
        c.full_name,
        c.title,
        f"Email : {c.email} | Tél : {c.phone}",
        c.address,
    ]
    if c.birth:
        lines.append(f"Né(e) le {c.birth} — Nationalité : française")
    lines += [
        "",
        f"Localisation : {c.city} | Mobilité : {', '.join(c.mobility)}",
        f"Disponibilité : {avail}",
        f"TJM : {c.rate} €",
        f"Années d'expérience : {int(c.years)} ans",
        "",
        "PROFIL",
        f"{c.title} avec {int(c.years)} ans d'expérience, intervenu(e) dans les secteurs "
        f"{', '.join(sorted({SECTORS[e['company']] for e in c.experiences}))}.",
        "",
        "COMPÉTENCES",
        ", ".join(c.skills),
        "",
        "EXPÉRIENCES PROFESSIONNELLES",
    ]
    for e in c.experiences:
        lines += [
            f"{e['title']} — {e['company']} ({c.city})",
            f"{_fmt_month(e['start'])} – {_fmt_month(e['end'])}",
            f"{e['task']}. Environnement : {', '.join(e['skills'])}.",
            "",
        ]
    lines += [
        "FORMATION",
        education(c, today),
        "",
        "LANGUES",
        ", ".join(lg.capitalize() + (" (natif)" if lg == "français" else " (professionnel)")
                  for lg in c.languages),
    ]
    return "\n".join(lines)


def education(c: Consultant, today: date) -> str:
    year = today.year - int(c.years) - 1
    if c.archetype in {"pm"}:
        return f"Master Management des systèmes d'information ({year})"
    return f"Diplôme d'ingénieur en informatique ({year})"


# ==================================================================== besoins
def make_needs(n: int, today: date, rng: random.Random) -> list[Need]:
    archs = ["data_engineer", "ai_engineer", "java_backend", "data_scientist", "devops",
             "dotnet", "frontend", "automation", "data_engineer", "java_backend",
             "ai_engineer", "qa", "devops", "data_scientist", "pm", "automation",
             "data_engineer", "java_backend"]
    out = []
    for i in range(n):
        arch = archs[i % len(archs)]
        spec = ARCHETYPES[arch]
        client = rng.choice(COMPANIES)
        sen = rng.choices(["junior", "confirmé", "senior", "expert"], weights=[1, 4, 4, 1])[0]
        min_years = {"junior": 1, "confirmé": 3, "senior": 6, "expert": 10}[sen]
        required = rng.sample(spec["core"], min(len(spec["core"]), rng.randint(3, 4)))
        nice = rng.sample(spec["opt"], 2)
        city = rng.choices(["Toulouse", "Paris", "Lyon", "Bordeaux"], weights=[70, 15, 8, 7])[0]
        remote = rng.choices(["hybride", "sur_site", "full_remote"], weights=[6, 3, 1])[0]
        max_rate = int(spec["rate"] * {"junior": 0.85, "confirmé": 1.05, "senior": 1.25,
                                       "expert": 1.45}[sen] / 10) * 10
        out.append(Need(
            nid=f"GT-N{i + 1:02d}", client=client, sector=SECTORS[client], archetype=arch,
            title=f"{rng.choice(spec['titles'])} {sen}", seniority=sen, min_years=min_years,
            required=required, nice=nice, city=city, remote=remote,
            start=today + timedelta(days=rng.choice([0, 14, 30, 45])),
            duration=rng.choice([3, 6, 6, 9, 12]), max_rate=max_rate,
            languages=["anglais"] if rng.random() < 0.3 else [],
            contact=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            fmt=rng.choices(["txt", "md", "docx"], weights=[6, 3, 1])[0],
        ))
    return out


def need_text(nd: Need, today: date) -> str:
    remote_txt = {"hybride": "2 jours/semaine (hybride)", "sur_site": "aucun, présentiel",
                  "full_remote": "full remote"}[nd.remote]
    start = "ASAP" if nd.start <= today else nd.start.strftime("%d/%m/%Y")
    body = [
        f"FICHE DE BESOIN — RÉF {nd.nid}",
        f"Client : {nd.client} (secteur {nd.sector})",
        f"Intitulé : {nd.title}",
        f"Contexte : {rng_context(nd)}",
        f"Compétences obligatoires : {', '.join(nd.required)}",
        f"Compétences souhaitées : {', '.join(nd.nice)}",
        f"Expérience : {nd.min_years} ans minimum",
        f"Séniorité : {nd.seniority}",
        f"Lieu : {nd.city}",
        f"Télétravail : {remote_txt}",
        f"Démarrage : {start}",
        f"Durée : {nd.duration} mois",
        f"TJM max : {nd.max_rate} €",
    ]
    if nd.languages:
        body.append(f"Langues : {', '.join(nd.languages)} courant")
    body.append(f"Contact client : {nd.contact} — {nd.contact.split()[0].lower()}@"
                f"{nd.client.lower().replace(' ', '').replace('é', 'e')}.fr — 05 61 "
                f"{(sum(map(ord, nd.nid)) % 89) + 10} 22 33")
    if nd.fmt == "md":  # forme « email transféré »
        return ("Objet : Nouveau besoin " + nd.title + "\n\nBonjour,\n\nVoici un nouveau besoin "
                "reçu ce matin, merci de positionner des profils rapidement.\n\n"
                + "\n".join(body) + "\n\nBonne journée,\nL'équipe commerciale")
    return "\n".join(body)


def rng_context(nd: Need) -> str:
    return (f"Au sein de la DSI de {nd.client}, renfort d'une équipe sur un projet de "
            f"{TASKS[nd.archetype][0].lower()}.")


# ====================================================================== gold
def relevance(c: Consultant, nd: Need, today: date, rate_tol: float = 0.10,
              avail_tol: int = 30) -> int:
    """0 = non pertinent, 1 = acceptable, 2 = très pertinent (vérité terrain)."""
    if (c.availability - max(nd.start, today)).days > avail_tol:
        return 0
    if nd.remote != "full_remote":
        mob = {m.lower() for m in c.mobility}
        if not (c.city == nd.city or nd.city.lower() in mob or "france" in mob
                or (nd.remote == "hybride" and "full remote" in mob)):
            return 0
    if c.rate > nd.max_rate * (1 + rate_tol):
        return 0
    if c.years < 0.7 * nd.min_years or _sen_rank(_seniority(c.years)) < _sen_rank(nd.seniority) - 1:
        return 0
    if any(lg not in c.languages for lg in nd.languages):
        return 0
    have = set(c.skills)
    req_hit = len(set(nd.required) & have) / len(nd.required)
    if c.archetype == nd.archetype:
        if req_hit == 1.0 and c.years >= nd.min_years:
            return 2
        return 1 if req_hit >= 0.5 else 0
    if nd.archetype in ARCHETYPES[c.archetype]["adjacent"] and req_hit >= 0.5:
        return 1
    return 0


# ========================================================= marché / prospects
def make_job_posts(today: date, rng: random.Random, n: int = 240) -> list[dict]:
    posts = []
    trend_boost = {"ai_engineer": 1.8, "automation": 1.6}  # demande en hausse récente
    for _ in range(n):
        days_ago = rng.randint(0, 55)
        recent = days_ago < 28
        weights = {a: WEIGHTS[a] * (trend_boost.get(a, 1.0) if recent else 1.0) for a in WEIGHTS}
        arch = rng.choices(list(weights), weights=list(weights.values()))[0]
        spec = ARCHETYPES[arch]
        company = rng.choice(COMPANIES)
        skills = rng.sample(spec["core"], min(3, len(spec["core"]))) + rng.sample(spec["opt"], 2)
        posts.append({
            "published": (today - timedelta(days=days_ago)).isoformat(),
            "company": company, "sector": SECTORS[company],
            "city": rng.choices(list(CITY_WEIGHTS), weights=list(CITY_WEIGHTS.values()))[0],
            "title": rng.choice(spec["titles"]), "skills": "|".join(skills),
        })
    return posts


def make_prospects() -> list[dict]:
    roles = ["DSI", "Responsable Data", "Directeur technique", "Responsable des achats IT"]
    out = []
    for i, company in enumerate(COMPANIES[:12]):
        out.append({"company": company, "sector": SECTORS[company],
                    "city": "Toulouse" if i % 3 else "Paris",
                    "contact_role": roles[i % len(roles)],
                    "opt_out": "true" if i == 5 else "false"})
    return out


def boond_export(consultants: list[Consultant], needs: list[Need], today: date) -> tuple[list, list]:
    res = []
    for c in consultants[:12]:
        res.append({"id": c.cid.replace("GT-C", "R"), "type": "resource", "attributes": {
            "firstName": c.first, "lastName": c.last, "title": c.title, "town": c.city,
            "mobilityAreas": c.mobility, "skills": c.skills, "languages": c.languages,
            "availability": "immédiate" if c.availability <= today
            else c.availability.strftime("%d/%m/%Y"),
            "averageDailyPriceExcludingTax": c.rate,
            "experiences": [{"title": e["title"], "company": e["company"],
                             "start": _fmt_month(e["start"]), "end": _fmt_month(e["end"]),
                             "description": e["task"]} for e in c.experiences],
        }})
    opp = [{"id": nd.nid.replace("GT-N", "O"), "type": "opportunity", "attributes": {
        "title": nd.title, "company": nd.client, "place": nd.city,
        "startDate": nd.start.isoformat(), "skills": nd.required,
        "averageDailyPriceExcludingTax": nd.max_rate}} for nd in needs[:4]]
    return res, opp


# ===================================================================== écriture
def _write_docx(path: Path, text: str) -> None:
    import docx
    from docx.shared import Pt

    d = docx.Document()
    for i, line in enumerate(text.splitlines()):
        p = d.add_paragraph(line)
        if i == 0 or line.isupper():
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(14 if i == 0 else 11)
    d.save(path)


def generate(out_dir: str | Path = "data", n_consultants: int = 60, n_needs: int = 15,
             seed: int = 42, today: date | None = None) -> dict:
    today = today or date.today()
    rng = random.Random(seed)
    root = Path(out_dir)
    syn = root / "synthetic"
    for sub in ("cvs", "needs"):
        (syn / sub).mkdir(parents=True, exist_ok=True)
        for old in (syn / sub).glob("*"):
            old.unlink()
    (root / "market").mkdir(parents=True, exist_ok=True)
    (root / "eval").mkdir(parents=True, exist_ok=True)

    needs = make_needs(n_needs, today, rng)
    n_random = max(10, n_consultants - 2 * n_needs - (n_needs + 1) // 2)
    consultants = make_consultants(n_random, today, rng, planted_for=needs)

    files = {}
    for c in consultants:
        text = consultant_text(c, today)
        name = f"CV_{c.cid}_{c.last}.{c.fmt}".replace(" ", "_")
        path = syn / "cvs" / name
        if c.fmt == "docx":
            _write_docx(path, text)
        else:
            path.write_text(text, encoding="utf-8")
        files[c.cid] = name
    need_files = {}
    for nd in needs:
        text = need_text(nd, today)
        name = f"Besoin_{nd.nid}_{nd.client.replace(' ', '_')}.{nd.fmt}"
        path = syn / "needs" / name
        if nd.fmt == "docx":
            _write_docx(path, text)
        else:
            path.write_text(text, encoding="utf-8")
        need_files[nd.nid] = name

    gold = {nd.nid: {c.cid: relevance(c, nd, today) for c in consultants} for nd in needs}

    truth = {
        "reference_date": today.isoformat(),
        "seed": seed,
        "consultants": {c.cid: {
            "file": files[c.cid], "archetype": c.archetype, "title": c.title,
            "years": c.years, "skills": c.skills, "city": c.city, "mobility": c.mobility,
            "availability": c.availability.isoformat(), "rate": c.rate,
            "languages": c.languages,
            "pii": [c.full_name, c.email, c.phone, c.address] + ([c.birth] if c.birth else []),
        } for c in consultants},
        "needs": {nd.nid: {
            "file": need_files[nd.nid], "archetype": nd.archetype, "title": nd.title,
            "required": nd.required, "nice": nd.nice, "min_years": nd.min_years,
            "seniority": nd.seniority, "city": nd.city, "remote": nd.remote,
            "start": nd.start.isoformat(), "max_rate": nd.max_rate, "languages": nd.languages,
            "pii": [nd.contact],
        } for nd in needs},
        "gold": gold,
    }
    (syn / "ground_truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")

    with (root / "eval" / "annotations_a_relire.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["need_id", "need_title", "candidate_id", "candidate_title", "gold_auto",
                    "gold_humain (à remplir)", "commentaire"])
        for nd in needs:
            for c in consultants:
                g = gold[nd.nid][c.cid]
                if g > 0 or (c.archetype == nd.archetype):
                    w.writerow([nd.nid, nd.title, c.cid, c.title, g, "", ""])

    with (root / "market" / "job_posts.csv").open("w", newline="", encoding="utf-8") as f:
        posts = make_job_posts(today, rng)
        w = csv.DictWriter(f, fieldnames=list(posts[0]))
        w.writeheader()
        w.writerows(posts)
    with (root / "market" / "prospects.csv").open("w", newline="", encoding="utf-8") as f:
        prospects = make_prospects()
        w = csv.DictWriter(f, fieldnames=list(prospects[0]))
        w.writeheader()
        w.writerows(prospects)

    res, opp = boond_export(consultants, needs, today)
    (syn / "boond_resources.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    (syn / "boond_opportunities.json").write_text(json.dumps(opp, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    pos = sum(1 for n in gold.values() for g in n.values() if g > 0)
    return {"consultants": len(consultants), "needs": len(needs), "relevant_pairs": pos,
            "out_dir": str(root)}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(generate(), indent=2, ensure_ascii=False))
