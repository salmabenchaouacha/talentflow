"""Connecteur BoondManager (ERP/CRM des ESN) : vivier de ressources, opportunités,
positionnements.

Authentification « client » de l'API Boond : en-tête X-Jwt-Client-BoondManager (JWT HS256
signé avec la clé client). Les chemins et champs ci-dessous suivent l'API REST publique de
BoondManager ; à vérifier avec la documentation et les droits de l'instance cible.

Le mode démo pointe vers le faux serveur Boond intégré à l'API (`/mock/boond`), alimenté
par des données synthétiques.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def boond_jwt(client_token: str, user_token: str, client_key: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"userToken": user_token, "clientToken": client_token,
               "time": int(time.time()), "mode": "normal"}
    signing = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    sig = hmac.new(client_key.encode(), signing.encode(), hashlib.sha256).digest()
    return f"{signing}.{_b64(sig)}"


@dataclass
class BoondClient:
    base_url: str
    client_token: str = ""
    user_token: str = ""
    client_key: str = ""
    timeout_s: int = 30

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.client_key:
            headers["X-Jwt-Client-BoondManager"] = boond_jwt(
                self.client_token, self.user_token, self.client_key
            )
        return headers

    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None) -> dict:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read() or b"{}")

    # ------------------------------------------------------------- lecture
    def list_resources(self, keywords: str = "", max_results: int = 100) -> list[dict]:
        """Ressources (consultants) du vivier."""
        out = self._request("GET", "/resources",
                            {"keywords": keywords, "maxResults": max_results})
        return out.get("data", [])

    def list_opportunities(self, state: str = "", max_results: int = 50) -> list[dict]:
        """Opportunités (besoins clients) ouvertes."""
        params = {"maxResults": max_results}
        if state:
            params["opportunityStates"] = state
        return self._request("GET", "/opportunities", params).get("data", [])

    # ------------------------------------------------------------ écriture
    def create_positioning(self, opportunity_id: str, resource_id: str,
                           comment: str = "") -> dict:
        """Positionne un consultant sur une opportunité (après validation humaine)."""
        body = {"data": {
            "attributes": {"informationComments": comment[:1000]},
            "relationships": {
                "opportunity": {"data": {"id": opportunity_id, "type": "opportunity"}},
                "dependsOn": {"data": {"id": resource_id, "type": "resource"}},
            },
        }}
        return self._request("POST", "/positionings", body=body)


def resource_to_text(resource: dict) -> str:
    """Transforme une ressource Boond en texte « CV » pour la chaîne d'extraction."""
    a = resource.get("attributes", resource)
    lines = [
        f"{a.get('firstName', '')} {a.get('lastName', '')}".strip(),
        a.get("title", "Consultant"),
        f"Localisation : {a.get('town', '')}",
        f"Mobilité : {', '.join(a.get('mobilityAreas', []))}",
        f"Disponibilité : {a.get('availability', '')}",
        f"TJM : {a.get('averageDailyPriceExcludingTax', '')} €",
        "",
        "COMPÉTENCES",
        ", ".join(a.get("skills", [])),
        "",
        "EXPÉRIENCES PROFESSIONNELLES",
    ]
    for exp in a.get("experiences", []):
        lines += [f"{exp.get('title')} — {exp.get('company')}",
                  f"{exp.get('start')} – {exp.get('end', 'Présent')}",
                  exp.get("description", "")]
    lines += ["", "LANGUES", ", ".join(a.get("languages", []))]
    return "\n".join(lines)
