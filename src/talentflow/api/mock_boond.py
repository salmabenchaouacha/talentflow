"""Faux serveur BoondManager pour la démo et les tests d'intégration.

Il sert les fichiers data/synthetic/boond_resources.json et boond_opportunities.json
avec la même forme de réponse (JSON:API) que l'API Boond, et accepte les positionnements.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/mock/boond", tags=["mock-boond"])
_DATA = Path("data/synthetic")
_POSITIONINGS: list[dict] = []


def _load(name: str) -> list[dict]:
    path = _DATA / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


@router.get("/resources")
def resources(keywords: str = "", maxResults: int = 100) -> dict:  # noqa: N803
    items = _load("boond_resources.json")
    if keywords:
        k = keywords.lower()
        items = [i for i in items if k in json.dumps(i, ensure_ascii=False).lower()]
    return {"meta": {"totals": {"rows": len(items)}}, "data": items[:maxResults]}


@router.get("/opportunities")
def opportunities(maxResults: int = 50) -> dict:  # noqa: N803
    items = _load("boond_opportunities.json")
    return {"meta": {"totals": {"rows": len(items)}}, "data": items[:maxResults]}


@router.post("/positionings")
def create_positioning(body: dict) -> dict:
    item = {"id": str(len(_POSITIONINGS) + 1), "type": "positioning", **body.get("data", {})}
    _POSITIONINGS.append(item)
    return {"data": item}
