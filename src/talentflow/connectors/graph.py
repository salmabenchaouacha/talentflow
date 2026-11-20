"""Connecteur Microsoft 365 via Microsoft Graph (flux « client credentials »).

Usages : lire les pièces jointes d'une boîte partagée (ex. besoins@entreprise.fr),
envoyer un dossier validé, déposer les dossiers dans SharePoint / OneDrive.
Permissions applicatives à accorder dans Entra ID : Mail.Read, Mail.Send,
Sites.ReadWrite.All (limiter avec une Application Access Policy à la boîte concernée).
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

GRAPH = "https://graph.microsoft.com/v1.0"


@dataclass
class GraphClient:  # pragma: no cover - nécessite un tenant Microsoft 365
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str
    _token: str = field(default="", repr=False)
    _expires: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode()
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
            payload = json.loads(r.read())
        self._token = payload["access_token"]
        self._expires = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{GRAPH}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self._get_token()}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    def unread_attachments(self, folder: str = "inbox") -> list[dict]:
        """[{message_id, subject, sender, filename, content(bytes)}] des emails non lus."""
        msgs = self._call(
            "GET",
            f"/users/{self.mailbox}/mailFolders/{folder}/messages"
            "?$filter=isRead eq false and hasAttachments eq true&$top=25",
        ).get("value", [])
        out = []
        for m in msgs:
            atts = self._call("GET", f"/users/{self.mailbox}/messages/{m['id']}/attachments")
            for a in atts.get("value", []):
                if a.get("@odata.type") == "#microsoft.graph.fileAttachment":
                    out.append({
                        "message_id": m["id"],
                        "subject": m.get("subject", ""),
                        "sender": m.get("from", {}).get("emailAddress", {}).get("address"),
                        "filename": a["name"],
                        "content": base64.b64decode(a["contentBytes"]),
                    })
            self._call("PATCH", f"/users/{self.mailbox}/messages/{m['id']}", {"isRead": True})
        return out

    def send_mail(self, to: list[str], subject: str, html_body: str,
                  attachments: list[tuple[str, bytes]] | None = None) -> None:
        message = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": t}} for t in to],
            "attachments": [
                {"@odata.type": "#microsoft.graph.fileAttachment", "name": name,
                 "contentBytes": base64.b64encode(content).decode()}
                for name, content in (attachments or [])
            ],
        }
        self._call("POST", f"/users/{self.mailbox}/sendMail",
                   {"message": message, "saveToSentItems": True})

    def upload_to_drive(self, drive_path: str, content: bytes) -> dict:
        """Dépôt d'un fichier < 4 Mo dans le OneDrive de la boîte (ex. 'Dossiers/x.docx')."""
        req = urllib.request.Request(
            f"{GRAPH}/users/{self.mailbox}/drive/root:/{urllib.parse.quote(drive_path)}:/content",
            data=content, method="PUT",
            headers={"Authorization": f"Bearer {self._get_token()}",
                     "Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
