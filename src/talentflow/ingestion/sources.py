"""Sources d'entrée : dossier surveillé et boîte mail IMAP.

En production, la réception par email est faite par n8n (workflow 01) qui appelle l'API.
Ce module permet aussi de fonctionner sans n8n (CLI `talentflow ingest-inbox`,
tâche Airflow de rattrapage).
"""

from __future__ import annotations

import email
import imaplib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from email.header import decode_header, make_header
from pathlib import Path
from typing import Literal

from talentflow.ingestion.parsers import SUPPORTED

DocKind = Literal["cv", "need"]

_NEED_HINTS = re.compile(
    r"\b(besoin|appel d['’]offres?|ao\b|fiche de poste|mission|recherche (?:un|une)|"
    r"compétences requises|démarrage|tjm max|profil recherché)",
    re.IGNORECASE,
)
_CV_HINTS = re.compile(
    r"\b(cv|curriculum|candidature|dossier de compétences|expériences? professionnelles?|"
    r"formation|disponibilité)\b",
    re.IGNORECASE,
)


@dataclass
class IncomingDocument:
    filename: str
    data: bytes
    kind: DocKind
    source: str  # "folder:<path>" | "imap:<message-id>" | "api"
    sender: str | None = None
    subject: str | None = None


def classify(filename: str, subject: str = "", text_preview: str = "") -> DocKind:
    """Devine si un document est un CV ou une fiche de besoin."""
    haystack = f"{filename} {subject}"
    if re.search(r"\b(besoin|ao|fiche[_ -]?poste|need)\b", haystack, re.IGNORECASE):
        return "need"
    if re.search(r"\b(cv|resume|candidature)\b", haystack, re.IGNORECASE):
        return "cv"
    need_score = len(_NEED_HINTS.findall(text_preview))
    cv_score = len(_CV_HINTS.findall(text_preview))
    return "need" if need_score > cv_score else "cv"


def iter_folder(folder: str | Path, kind: DocKind | None = None) -> Iterator[IncomingDocument]:
    """Parcourt un dossier (ex. data/inbox/cvs) et renvoie les documents supportés."""
    root = Path(folder)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED:
            data = path.read_bytes()
            preview = data[:4000].decode("utf-8", errors="ignore")
            yield IncomingDocument(
                filename=path.name,
                data=data,
                kind=kind or classify(path.name, text_preview=preview),
                source=f"folder:{path}",
            )


def iter_imap(
    host: str,
    user: str,
    password: str,
    folder: str = "INBOX",
    unseen_only: bool = True,
    mark_seen: bool = True,
) -> Iterator[IncomingDocument]:  # pragma: no cover - nécessite un serveur IMAP
    """Lit les pièces jointes des emails non lus d'une boîte IMAP (Outlook, Gmail…)."""
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, password)
        conn.select(folder)
        _, ids = conn.search(None, "UNSEEN" if unseen_only else "ALL")
        for msg_id in ids[0].split():
            _, payload = conn.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(payload[0][1])
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            sender = msg.get("From")
            for part in msg.walk():
                fname = part.get_filename()
                if not fname:
                    continue
                fname = str(make_header(decode_header(fname)))
                if Path(fname).suffix.lower() not in SUPPORTED:
                    continue
                data = part.get_payload(decode=True) or b""
                yield IncomingDocument(
                    filename=fname,
                    data=data,
                    kind=classify(fname, subject),
                    source=f"imap:{msg.get('Message-ID', msg_id.decode())}",
                    sender=sender,
                    subject=subject,
                )
            if mark_seen:
                conn.store(msg_id, "+FLAGS", "\\Seen")
    finally:
        conn.logout()
