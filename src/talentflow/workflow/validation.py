"""Validation humaine (human-in-the-loop) : aucun profil n'est envoyé sans décision.

Machine à états d'une proposition :
    pending_review ──valider──▶ validated ──envoyer (n8n)──▶ sent
          └────────rejeter────▶ rejected
Chaque transition est journalisée (qui, quand, pourquoi) dans `audit_log`.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from talentflow.schemas import MatchStatus
from talentflow.storage.repository import Repository, utcnow

log = logging.getLogger(__name__)

TRANSITIONS: dict[MatchStatus, set[MatchStatus]] = {
    MatchStatus.PENDING: {MatchStatus.VALIDATED, MatchStatus.REJECTED},
    MatchStatus.VALIDATED: {MatchStatus.SENT, MatchStatus.REJECTED},
    MatchStatus.REJECTED: {MatchStatus.PENDING},  # réouverture possible
    MatchStatus.SENT: set(),
}


class TransitionError(ValueError):
    pass


def transition(repo: Repository, match_id: str, target: MatchStatus, actor: str,
               comment: str = "") -> dict:
    match = repo.get_match(match_id)
    if not match:
        raise KeyError(f"Proposition inconnue : {match_id}")
    current = MatchStatus(match["status"])
    if target not in TRANSITIONS[current]:
        raise TransitionError(f"Transition interdite : {current.value} → {target.value}")
    if target == MatchStatus.REJECTED and not comment.strip():
        raise TransitionError("Un motif de rejet est obligatoire (il sert à améliorer le matching)")

    fields: dict = {"status": target.value}
    if target in {MatchStatus.VALIDATED, MatchStatus.REJECTED}:
        fields.update(reviewed_at=utcnow(), reviewer=actor, review_comment=comment or None)
    if target == MatchStatus.SENT:
        fields["sent_at"] = utcnow()
    repo.update_match(match_id, **fields)
    repo.audit(actor, f"match_{target.value}", "match", match_id,
               {"from": current.value, "comment": comment, "need_id": match["need_id"],
                "candidate_id": match["candidate_id"]})
    return repo.get_match(match_id)


def notify_webhook(url: str, payload: dict, timeout_s: int = 10) -> bool:
    """Prévient n8n (workflow 02) qu'une proposition est validée. Non bloquant."""
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s):
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Webhook n8n injoignable (%s) : %s", url, exc)
        return False
