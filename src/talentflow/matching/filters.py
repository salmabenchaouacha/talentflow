"""Filtres métier : ce qu'un commercial vérifie avant même de lire un CV.

Un échec sur un critère bloquant exclut le profil (avec le motif, pour la traçabilité) ;
les écarts tolérés deviennent des « points d'attention » affichés au commercial.
"""

from __future__ import annotations

from datetime import date, timedelta

from talentflow.schemas import CandidateProfile, FilterResult, JobNeed, RemotePolicy

_REMOTE_WORDS = {"full remote", "remote", "teletravail", "france entiere", "france",
                 "toute la france", "100% remote", "teletravail complet"}


def _norm_city(value: str | None) -> str:
    """'Toulouse (31)', 'toulouse - hybride', 'Saint-Étienne' -> forme comparable."""
    import unicodedata

    v = (value or "").split("(")[0].split(",")[0].split(" — ")[0].split(" - ")[0]
    v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode()
    return v.strip().lower().replace("-", " ")


def check_location(c: CandidateProfile, n: JobNeed) -> tuple[bool, str | None]:
    if n.remote_policy == RemotePolicy.REMOTE or not n.location:
        return True, None
    city = _norm_city(n.location)
    if _norm_city(c.location) == city:
        return True, None
    mobility = {_norm_city(m) for m in c.mobility}
    if city in mobility:
        return True, None
    if mobility & {"france", "france entiere", "toute la france"}:
        return True, None
    if n.remote_policy == RemotePolicy.HYBRID and mobility & _REMOTE_WORDS:
        return True, "Mobilité en télétravail uniquement : à confirmer pour les jours sur site"
    return False, f"Localisation incompatible ({c.location or 'non précisée'} → {n.location})"


def apply_filters(
    c: CandidateProfile,
    n: JobNeed,
    today: date | None = None,
    rate_tolerance: float = 0.10,
    availability_tolerance_days: int = 30,
) -> FilterResult:
    today = today or date.today()
    reasons: list[str] = []
    warnings: list[str] = []

    # 1. Disponibilité
    start = n.start_date or today
    if c.availability_date:
        late = (c.availability_date - start).days
        if late > availability_tolerance_days:
            reasons.append(
                f"Disponible le {c.availability_date:%d/%m/%Y}, démarrage souhaité le {start:%d/%m/%Y}"
            )
        elif late > 0:
            warnings.append(f"Disponible {late} jour{'s' if late > 1 else ''} après la date de démarrage")
    else:
        warnings.append("Disponibilité non renseignée")

    # 2. Localisation / mobilité
    ok, msg = check_location(c, n)
    if not ok:
        reasons.append(msg or "Localisation incompatible")
    elif msg:
        warnings.append(msg)

    # 3. TJM
    if n.max_daily_rate and c.daily_rate:
        ceiling = n.max_daily_rate * (1 + rate_tolerance)
        if c.daily_rate > ceiling:
            reasons.append(f"TJM {c.daily_rate:.0f} € > budget {n.max_daily_rate:.0f} €")
        elif c.daily_rate > n.max_daily_rate:
            warnings.append(
                f"TJM {c.daily_rate:.0f} € légèrement au-dessus du budget ({n.max_daily_rate:.0f} €)"
            )

    # 4. Séniorité / années d'expérience
    if n.min_years and c.years_experience < 0.7 * n.min_years:
        reasons.append(f"{c.years_experience:g} ans d'expérience pour {n.min_years:g} demandés")
    elif n.min_years and c.years_experience < n.min_years:
        warnings.append(f"{c.years_experience:g} ans d'expérience pour {n.min_years:g} demandés")
    if n.seniority and c.seniority.rank < n.seniority.rank - 1:
        reasons.append(f"Séniorité {c.seniority.label} pour un besoin {n.seniority.label}")

    # 5. Langues exigées
    missing_langs = [lg for lg in n.languages if lg not in c.languages]
    if missing_langs:
        reasons.append(f"Langue(s) non mentionnée(s) : {', '.join(missing_langs)}")

    return FilterResult(passed=not reasons, reasons=reasons, warnings=warnings)


def availability_window(today: date, days: int) -> date:
    return today + timedelta(days=days)
