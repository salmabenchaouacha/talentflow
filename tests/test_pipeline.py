from datetime import date, timedelta

import docx
import pytest
from conftest import CV_TEXT, NEED_TEXT

from talentflow.schemas import MatchStatus
from talentflow.storage.metrics import weekly_kpis
from talentflow.workflow.validation import TransitionError, transition


def _seed(flow):
    cv = flow.ingest(CV_TEXT.encode(), "CV_Camille_Martin.txt", kind="cv")
    other = CV_TEXT.replace("Camille Martin", "Hugo Bernard").replace("Airflow", "Kafka") \
                   .replace("camille.martin", "hugo.bernard")
    flow.ingest(other.encode(), "cv2.txt", kind="cv")
    need = flow.ingest(NEED_TEXT.encode(), "besoin.txt")
    return cv, need


def test_ingestion_stores_only_pseudonymized_data(flow):
    cv, need = _seed(flow)
    assert need.kind == "need"  # classé automatiquement
    doc = flow.repo.one("SELECT filename, pseudo_text FROM documents WHERE document_id = ?",
                        (cv.document_id,))
    assert "Martin" not in doc["filename"] and "Martin" not in doc["pseudo_text"]
    assert flow.repo.get_vault(cv.document_id)["[PERSONNE_1]"] == "Camille Martin"
    assert cv.removed  # date de naissance, nationalité…


def test_duplicate_document_is_detected(flow):
    first = flow.ingest(CV_TEXT.encode(), "a.txt", kind="cv")
    again = flow.ingest(CV_TEXT.encode(), "b.txt", kind="cv")
    assert again.duplicate and again.entity_id == first.entity_id
    assert flow.repo.count("candidates") == 1


def test_match_validate_and_generate_dossier(flow):
    cv, need = _seed(flow)
    report = flow.match_need(need.entity_id)
    assert report.proposals[0].candidate_id == cv.entity_id
    match = flow.repo.list_matches(need_id=need.entity_id)[0]

    with pytest.raises(PermissionError):
        flow.generate_dossier(match["match_id"])

    transition(flow.repo, match["match_id"], MatchStatus.VALIDATED, "sales@esn.fr")
    path = flow.generate_dossier(match["match_id"], actor="sales@esn.fr")
    text = "\n".join(p.text for p in docx.Document(path).paragraphs)
    tables = "\n".join(c.text for t in docx.Document(path).tables for r in t.rows for c in r.cells)
    assert "C. M." in tables          # identité réduite aux initiales
    assert "Camille" not in text + tables
    assert "Airflow" in tables
    assert any(e["action"] == "match_validated" for e in flow.repo.list_audit())


def test_rejection_requires_reason_and_forbidden_transitions(flow):
    _, need = _seed(flow)
    flow.match_need(need.entity_id)
    mid = flow.repo.list_matches(need_id=need.entity_id)[0]["match_id"]
    with pytest.raises(TransitionError):
        transition(flow.repo, mid, MatchStatus.REJECTED, "sales")
    with pytest.raises(TransitionError):
        transition(flow.repo, mid, MatchStatus.SENT, "n8n")
    transition(flow.repo, mid, MatchStatus.REJECTED, "sales", "pas d'expérience aéro")
    assert flow.repo.get_match(mid)["review_comment"] == "pas d'expérience aéro"


def test_rematch_keeps_past_decisions(flow):
    _, need = _seed(flow)
    flow.match_need(need.entity_id)
    mid = flow.repo.list_matches(need_id=need.entity_id)[0]["match_id"]
    transition(flow.repo, mid, MatchStatus.VALIDATED, "sales")
    flow.match_need(need.entity_id)
    statuses = [m["status"] for m in flow.repo.list_matches(need_id=need.entity_id)]
    assert statuses.count("validated") == 1


def test_gdpr_erasure_and_retention(flow):
    cv, need = _seed(flow)
    flow.match_need(need.entity_id)
    assert flow.repo.erase_candidate(cv.entity_id, "dpo@esn.fr", "demande art. 17")
    assert flow.repo.get_candidate(cv.entity_id) is None
    assert flow.repo.get_vault(cv.document_id) == {}
    assert not [m for m in flow.repo.list_matches() if m["candidate_id"] == cv.entity_id]

    flow.today = date.today() + timedelta(days=flow.settings.retention_days + 1)
    erased = flow.purge_expired()
    assert len(erased) == 1 and flow.repo.count("candidates") == 0


def test_weekly_kpis(flow):
    _, need = _seed(flow)
    flow.match_need(need.entity_id)
    mid = flow.repo.list_matches()[0]["match_id"]
    transition(flow.repo, mid, MatchStatus.VALIDATED, "sales")
    week = weekly_kpis(flow.repo)[-1]
    assert week["needs_received"] == 1 and week["cvs_ingested"] == 2
    assert week["validated"] == 1 and week["validation_rate"] == 1.0


def test_prospection_and_market(flow, tmp_path):
    from talentflow.evaluation.synthetic import generate

    generate(flow.settings.data_dir, n_consultants=20, n_needs=4, seed=1, today=flow.today)
    for p in sorted((flow.settings.data_dir / "synthetic" / "cvs").iterdir()):
        flow.ingest_path(p, kind="cv")
    ids = flow.generate_prospection()
    assert ids
    email = flow.repo.list_prospection("draft")[0]
    assert "STOP" in email["body"]
    for c in flow.repo.list_candidates():  # aucune identité dans un email de prospection
        assert c.candidate_id not in email["body"]
    report = flow.market_report()
    assert report["gaps"] and report["trends"]


def test_reset_empties_database(flow):
    _seed(flow)
    flow.repo.reset()
    assert flow.repo.count("candidates") == 0 and flow.repo.count("documents") == 0
