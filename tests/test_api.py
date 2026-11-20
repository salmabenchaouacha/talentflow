"""Tests de l'API (ignorés si FastAPI n'est pas installé)."""

import pytest

fastapi = pytest.importorskip("fastapi")
from conftest import CV_TEXT, NEED_TEXT  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TF_DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("TF_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("TF_API_KEY", "secret")
    from talentflow.api import main

    main.get_flow.cache_clear()
    return TestClient(main.app), {"X-API-Key": "secret"}


def test_auth_required(client):
    c, _ = client
    assert c.get("/health").status_code == 200
    assert c.get("/needs").status_code == 401


def test_full_flow(client):
    c, h = client
    r = c.post("/documents", headers=h, files={"file": ("cv.txt", CV_TEXT.encode())},
               data={"kind": "cv"})
    assert r.status_code == 200 and r.json()["kind"] == "cv"
    r = c.post("/documents", headers=h, files={"file": ("besoin.txt", NEED_TEXT.encode())},
               data={"kind": ""})
    need_id = r.json()["entity_id"]
    assert r.json()["kind"] == "need"

    res = c.post(f"/needs/{need_id}/match", headers=h).json()
    assert res["proposals"]
    mid = c.get("/matches", headers=h, params={"need_id": need_id}).json()[0]["match_id"]

    assert c.post(f"/matches/{mid}/reject", headers=h, json={"reviewer": "x"}).status_code == 409
    v = c.post(f"/matches/{mid}/validate", headers=h, json={"reviewer": "sales"}).json()
    assert v["event"] == "match_validated" and v["n8n_notified"] is False
    doc = c.get(f"/matches/{mid}/dossier", headers=h)
    assert doc.status_code == 200 and doc.content[:2] == b"PK"
    assert c.post(f"/matches/{mid}/sent", headers=h).json()["status"] == "sent"
    assert c.get("/metrics/weekly", headers=h).json()["weeks"][-1]["sent"] == 1
