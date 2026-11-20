"""API REST de TalentFlow (FastAPI). Point d'entrée de n8n, du Streamlit et des intégrations.

Lancement : uvicorn talentflow.api.main:app --reload
Documentation interactive : http://localhost:8000/docs
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from talentflow import __version__
from talentflow.config import get_settings
from talentflow.pipeline import TalentFlow
from talentflow.schemas import MatchStatus
from talentflow.storage.metrics import pool_overview, rejection_reasons, weekly_kpis
from talentflow.workflow.validation import TransitionError, notify_webhook, transition

app = FastAPI(
    title="TalentFlow API",
    version=__version__,
    description="Chaîne automatisée de staffing par IA : besoins clients → profils validés.",
)


@lru_cache(maxsize=1)
def get_flow() -> TalentFlow:
    return TalentFlow.build(get_settings())


def require_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != get_flow().settings.api_key:
        raise HTTPException(status_code=401, detail="Clé d'API invalide (en-tête X-API-Key)")


class Review(BaseModel):
    reviewer: str
    comment: str = ""


# ------------------------------------------------------------------ santé
@app.get("/health")
def health() -> dict:
    flow = get_flow()
    return {
        "status": "ok", "version": __version__,
        "llm_provider": flow.settings.llm_provider,
        "embedding_provider": flow.settings.embedding_provider,
        "database": "postgresql" if flow.repo.pg else "sqlite",
    }


# -------------------------------------------------------------- ingestion
@app.post("/documents", dependencies=[Depends(require_key)])
async def upload_document(
    file: UploadFile = File(...),
    kind: str | None = Form(default=None),
    source: str = Form(default="api"),
    sender: str | None = Form(default=None),
) -> dict:
    """kind = "cv" | "need" ; vide → classification automatique (objet, nom, contenu)."""
    if kind not in (None, "", "cv", "need"):
        raise HTTPException(422, "kind doit valoir 'cv', 'need' ou être vide")
    kind = kind or None
    data = await file.read()
    try:
        res = get_flow().ingest(data, file.filename or "document.txt", kind=kind,
                                source=source, sender=sender, actor=source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return res.__dict__


@app.post("/cvs", dependencies=[Depends(require_key)])
async def upload_cv(file: UploadFile = File(...), source: str = Form(default="api")) -> dict:
    return await upload_document(file=file, kind="cv", source=source, sender=None)


@app.post("/needs", dependencies=[Depends(require_key)])
async def upload_need(file: UploadFile = File(...), source: str = Form(default="api"),
                      sender: str | None = Form(default=None)) -> dict:
    return await upload_document(file=file, kind="need", source=source, sender=sender)


# ------------------------------------------------------------- consultation
@app.get("/candidates", dependencies=[Depends(require_key)])
def list_candidates() -> list[dict]:
    return [c.model_dump(mode="json", include={"candidate_id", "title", "seniority",
                                               "years_experience", "location",
                                               "availability_date"})
            for c in get_flow().repo.list_candidates()]


@app.get("/candidates/{candidate_id}", dependencies=[Depends(require_key)])
def get_candidate(candidate_id: str) -> dict:
    c = get_flow().repo.get_candidate(candidate_id)
    if not c:
        raise HTTPException(404, "Candidat inconnu")
    return c.model_dump(mode="json")


@app.get("/needs", dependencies=[Depends(require_key)])
def list_needs(status: str | None = None) -> list[dict]:
    return get_flow().repo.list_needs(status)


@app.get("/needs/{need_id}", dependencies=[Depends(require_key)])
def get_need(need_id: str) -> dict:
    n = get_flow().repo.get_need(need_id)
    if not n:
        raise HTTPException(404, "Besoin inconnu")
    return n.model_dump(mode="json")


# ---------------------------------------------------------------- matching
@app.post("/needs/{need_id}/match", dependencies=[Depends(require_key)])
def match_need(need_id: str, mode: Literal["vector", "rules", "full"] = "full") -> dict:
    flow = get_flow()
    try:
        report = flow.match_need(need_id, mode=mode)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    need = flow.repo.get_need(need_id)
    return {
        "need_id": need_id,
        "need_title": need.title if need else None,
        "retrieved": report.retrieved,
        "eligible": len(report.ranked),
        "proposals": [r.model_dump(mode="json") for r in report.proposals],
        "excluded": [{"candidate_id": r.candidate_id, "reasons": r.filters.reasons}
                     for r in report.excluded[:10]],
        "review_url": f"{flow.settings.review_ui_url}/?need_id={need_id}",
    }


@app.get("/matches", dependencies=[Depends(require_key)])
def list_matches(need_id: str | None = None, status: str | None = None) -> list[dict]:
    return get_flow().repo.list_matches(need_id=need_id, status=status)


@app.post("/matches/{match_id}/validate", dependencies=[Depends(require_key)])
def validate_match(match_id: str, review: Review) -> dict:
    flow = get_flow()
    try:
        match = transition(flow.repo, match_id, MatchStatus.VALIDATED, review.reviewer,
                           review.comment)
    except (KeyError, TransitionError) as exc:
        raise HTTPException(409, str(exc)) from exc
    path = flow.generate_dossier(match_id, actor=review.reviewer)
    payload = {
        "event": "match_validated", "match_id": match_id, "need_id": match["need_id"],
        "need_title": match["need_title"], "client": match["client"],
        "candidate_id": match["candidate_id"], "reviewer": review.reviewer,
        "dossier_url": f"{flow.settings.api_base_url}/matches/{match_id}/dossier",
        "dossier_filename": path.name,
    }
    payload["n8n_notified"] = notify_webhook(flow.settings.n8n_validation_webhook_url, payload)
    return payload


@app.post("/matches/{match_id}/reject", dependencies=[Depends(require_key)])
def reject_match(match_id: str, review: Review) -> dict:
    try:
        return transition(get_flow().repo, match_id, MatchStatus.REJECTED, review.reviewer,
                          review.comment)
    except (KeyError, TransitionError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/matches/{match_id}/sent", dependencies=[Depends(require_key)])
def mark_sent(match_id: str, actor: str = Query(default="n8n")) -> dict:
    try:
        return transition(get_flow().repo, match_id, MatchStatus.SENT, actor)
    except (KeyError, TransitionError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/matches/{match_id}/dossier", dependencies=[Depends(require_key)])
def download_dossier(match_id: str) -> FileResponse:
    flow = get_flow()
    match = flow.repo.get_match(match_id)
    if not match or not match.get("dossier_path"):
        raise HTTPException(404, "Dossier non généré (la proposition doit être validée)")
    return FileResponse(
        match["dossier_path"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=match["dossier_path"].rsplit("/", 1)[-1],
    )


# ------------------------------------------------------------ pilotage
@app.get("/metrics/weekly", dependencies=[Depends(require_key)])
def metrics_weekly(weeks: int = 8) -> dict:
    flow = get_flow()
    return {"weeks": weekly_kpis(flow.repo, weeks), "pool": pool_overview(flow.repo),
            "rejection_reasons": rejection_reasons(flow.repo)}


@app.get("/market", dependencies=[Depends(require_key)])
def market() -> dict:
    return get_flow().market_report()


# ------------------------------------------------------------ prospection
@app.post("/prospection/generate", dependencies=[Depends(require_key)])
def prospection_generate() -> dict:
    ids = get_flow().generate_prospection(actor="api")
    return {"created": len(ids), "email_ids": ids}


@app.get("/prospection", dependencies=[Depends(require_key)])
def prospection_list(status: str | None = None) -> list[dict]:
    return get_flow().repo.list_prospection(status)


@app.post("/prospection/{email_id}/{action}", dependencies=[Depends(require_key)])
def prospection_action(email_id: str, action: Literal["approve", "reject", "sent"],
                       actor: str = Query(default="api")) -> dict:
    status = {"approve": "approved", "reject": "rejected", "sent": "sent"}[action]
    flow = get_flow()
    flow.repo.update_prospection(email_id, status)
    flow.repo.audit(actor, f"prospection_{status}", "prospection", email_id)
    return {"email_id": email_id, "status": status}


# ------------------------------------------------------------------ RGPD
@app.delete("/candidates/{candidate_id}", dependencies=[Depends(require_key)])
def erase_candidate(candidate_id: str, actor: str = Query(...),
                    reason: str = Query(default="demande de la personne (art. 17)")) -> dict:
    if not get_flow().repo.erase_candidate(candidate_id, actor, reason):
        raise HTTPException(404, "Candidat inconnu")
    return {"erased": candidate_id}


@app.post("/gdpr/purge", dependencies=[Depends(require_key)])
def gdpr_purge() -> dict:
    return {"erased": get_flow().purge_expired()}


@app.get("/audit", dependencies=[Depends(require_key)])
def audit(limit: int = 200) -> list[dict]:
    return get_flow().repo.list_audit(limit)


# ------------------------------------------------------ faux Boond (démo)
if get_settings().env != "prod":
    from talentflow.api.mock_boond import router as mock_boond_router

    app.include_router(mock_boond_router)
