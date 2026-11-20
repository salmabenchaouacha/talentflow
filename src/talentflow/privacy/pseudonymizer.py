"""Pseudonymisation RGPD des documents avant tout envoi à un LLM.

Principe (art. 4.5 et 25 du RGPD, « privacy by design ») :
- les données directement identifiantes sont remplacées par des jetons ([EMAIL_1], [PERSONNE_1]…) ;
- la table de correspondance (le « coffre ») est stockée à part, jamais envoyée au LLM ;
- les données sensibles inutiles au matching (âge, date de naissance, situation familiale,
  nationalité, n° de sécurité sociale) sont supprimées, pas seulement masquées.

Détection : expressions régulières + heuristiques de mise en page, et Presidio (NER)
en option (`TF_USE_PRESIDIO=true`) pour les noms de personnes dans le texte libre.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Mots qui indiquent qu'une ligne n'est pas un nom de personne.
_NOT_A_NAME = {
    "curriculum", "vitae", "cv", "profil", "ingénieur", "ingenieur", "développeur",
    "developpeur", "consultant", "consultante", "data", "chef", "projet", "expérience",
    "experience", "compétences", "competences", "formation", "langues", "senior",
    "junior", "lead", "architecte", "devops", "fullstack", "full", "stack", "scientist",
    "engineer", "analyst", "analyste", "manager", "responsable", "besoin", "mission",
    "client", "fiche", "contact", "disponibilité", "localisation", "tjm", "qa", "testeur",
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("URL", re.compile(
        r"(?:https?://|www\.)\S+|(?:linkedin\.com|github\.com|gitlab\.com)/\S+",
        re.IGNORECASE,
    )),
    # NIR (n° sécurité sociale FR) : 13 chiffres + clé, avec ou sans espaces
    ("NIR", re.compile(r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}(?:\s?\d{2})?\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b")),
    # Téléphones : FR, TN, international
    ("TEL", re.compile(
        r"(?:(?:\+|00)\d{2,3}[\s.-]?)?(?:\(0\)[\s.-]?)?\d{1,2}(?:[\s.-]?\d{2}){4}\b"
        r"|(?:\+|00)216[\s.-]?\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b"
    )),
    ("ADRESSE", re.compile(
        r"\b\d{1,4}\s?(?:bis|ter)?,?\s+(?:rue|avenue|av\.|boulevard|bd|chemin|allée|allee|"
        r"impasse|place|route|quai|cours|résidence|residence)\b[^\n,]{2,60}"
        r"(?:,?\s*\d{5}\s+[A-ZÉÈ][\w\- ]+)?",
        re.IGNORECASE,
    )),
]

# Informations sensibles inutiles au matching : supprimées (ligne entière ou segment).
_DROP_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("DATE_NAISSANCE", re.compile(
        r"(?:\bn[ée]e?(?:\s?\(e\))?|date de naissance|naissance)\s*(?:le|:)?\s*"
        r"\d{1,2}[/.\- ]\w+[/.\- ]\d{2,4}",
        re.IGNORECASE,
    )),
    ("AGE", re.compile(
        r"[âa]ge\s*:?\s*\d{2}\s?ans|\(\s*\d{2}\s?ans\s*\)|^\s*\d{2}\s?ans\s*$",
        re.IGNORECASE | re.MULTILINE,
    )),
    ("SITUATION_FAMILIALE", re.compile(
        r"\b(?:mari[ée]e?|c[ée]libataire|pacs[ée]e?|divorc[ée]e?|veu[fv]e?)\b"
        r"(?:,?\s*\d+\s*enfants?)?",
        re.IGNORECASE,
    )),
    ("NATIONALITE", re.compile(r"nationalit[ée]\s*:?\s*[^\n,;]+", re.IGNORECASE)),
    ("PERMIS_SEJOUR", re.compile(r"(?:titre|permis) de s[ée]jour[^\n]*", re.IGNORECASE)),
]

_LABELLED_NAME = re.compile(
    r"^\s*(?:nom(?:\s+complet)?|prénom|prenom|name|candidat(?:e)?|contact(?:\s+client)?|"
    r"interlocut(?:eur|rice)|référent(?:e)?|manager|responsable)\s*:\s*([^\n—–|,;(]+)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class PseudonymizationResult:
    text: str
    vault: dict[str, str] = field(default_factory=dict)  # jeton -> valeur d'origine
    removed: dict[str, int] = field(default_factory=dict)  # catégorie -> nb suppressions

    @property
    def entity_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for token in self.vault:
            cat = token.strip("[]").rsplit("_", 1)[0]
            counts[cat] = counts.get(cat, 0) + 1
        return counts


class Pseudonymizer:
    def __init__(self, use_presidio: bool = False, language: str = "fr") -> None:
        self.use_presidio = use_presidio
        self.language = language
        self._analyzer = None
        if use_presidio:
            try:  # pragma: no cover - dépendance optionnelle
                from presidio_analyzer import AnalyzerEngine
                from presidio_analyzer.nlp_engine import NlpEngineProvider

                provider = NlpEngineProvider(nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "fr", "model_name": "fr_core_news_md"}],
                })
                self._analyzer = AnalyzerEngine(
                    nlp_engine=provider.create_engine(), supported_languages=["fr"]
                )
            except Exception:  # noqa: BLE001
                self._analyzer = None

    # ------------------------------------------------------------------ API
    def pseudonymize(self, text: str, known_names: list[str] | None = None) -> PseudonymizationResult:
        vault: dict[str, str] = {}
        reverse: dict[str, str] = {}
        counters: dict[str, int] = {}
        removed: dict[str, int] = {}

        def token_for(category: str, value: str) -> str:
            key = f"{category}:{value.strip().lower()}"
            if key in reverse:
                return reverse[key]
            counters[category] = counters.get(category, 0) + 1
            tok = f"[{category}_{counters[category]}]"
            vault[tok] = value.strip()
            reverse[key] = tok
            return tok

        out = text

        # 1) Suppressions (données sensibles inutiles)
        for cat, pat in _DROP_PATTERNS:
            out, n = pat.subn("", out)
            if n:
                removed[cat] = removed.get(cat, 0) + n

        # 2) Identifiants structurés
        for cat, pat in _PATTERNS:
            out = pat.sub(lambda m, c=cat: token_for(c, m.group(0)), out)

        # 3) Noms : champs étiquetés, en-tête de CV, noms connus, NER optionnel
        names = set(n for n in (known_names or []) if n and len(n) > 2)
        names.update(v for v in (m.group(1).strip() for m in _LABELLED_NAME.finditer(text))
                     if self._looks_like_name(v))
        header = self._header_name(text)
        if header:
            names.add(header)
        names.update(self._presidio_names(out))

        for full in sorted(names, key=len, reverse=True):
            tok = token_for("PERSONNE", full)
            out = re.sub(re.escape(full), tok, out, flags=re.IGNORECASE)
            # Chaque partie du nom isolément (ex. le prénom seul dans un e-mail de signature)
            for part in full.split():
                if len(part) > 2 and part.lower() not in _NOT_A_NAME:
                    out = re.sub(rf"(?<![\w\[]){re.escape(part)}(?![\w\]])", tok, out)

        out = re.sub(r"^[\s—–\-|,;:.]+$", "", out, flags=re.MULTILINE)  # lignes vidées
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return PseudonymizationResult(text=out.strip(), vault=vault, removed=removed)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _looks_like_name(value: str) -> bool:
        words = value.replace("-", " ").split()
        if not 2 <= len(words) <= 4 or any(ch.isdigit() for ch in value):
            return False
        if any(w.lower().strip(".,:") in _NOT_A_NAME for w in words):
            return False
        return all(w[0].isupper() for w in words if w)

    @staticmethod
    def _header_name(text: str) -> str | None:
        """Sur un CV, le nom est presque toujours la première ligne non vide."""
        for line in text.splitlines()[:4]:
            line = line.strip().strip("#*").strip()
            if not line:
                continue
            return line if Pseudonymizer._looks_like_name(line) else None
        return None

    def _presidio_names(self, text: str) -> set[str]:
        if not self._analyzer:
            return set()
        results = self._analyzer.analyze(  # pragma: no cover
            text=text, language=self.language, entities=["PERSON"], score_threshold=0.6
        )
        return {text[r.start:r.end] for r in results if "[" not in text[r.start:r.end]}


def reidentify(text: str, vault: dict[str, str], allowed: set[str] | None = None) -> str:
    """Ré-identifie les jetons autorisés (ex. après validation humaine)."""
    for tok, value in vault.items():
        cat = tok.strip("[]").rsplit("_", 1)[0]
        if allowed is None or cat in allowed:
            text = text.replace(tok, value)
    return text


def initials(full_name: str) -> str:
    """'Jean-Marc Durand' -> 'J.-M. D.' : identité masquée pour le dossier client."""
    parts = []
    for word in full_name.split():
        sub = [p[0].upper() + "." for p in word.split("-") if p]
        parts.append("-".join(sub))
    return " ".join(parts)


def mask_with_vault(text: str, vault: dict[str, str]) -> str:
    """Applique un coffre existant à un autre texte (ex. le nom du fichier « CV_Nom.pdf »)."""
    for tok, value in sorted(vault.items(), key=lambda kv: -len(kv[1])):
        text = re.sub(re.escape(value), tok, text, flags=re.IGNORECASE)
        if tok.startswith("[PERSONNE"):
            for part in value.split():
                if len(part) > 2:
                    text = re.sub(re.escape(part), tok, text, flags=re.IGNORECASE)
    return text
