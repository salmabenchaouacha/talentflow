"""Persistance : SQLite (dev, tests, démo) ou PostgreSQL + PGVector (production).

Le même code SQL sert aux deux moteurs (placeholders `?` convertis en `%s` pour Postgres).
Les données personnelles réelles ne sont stockées que dans `pii_vault`, séparée du reste,
et effacées au droit à l'effacement ou à l'expiration de la durée de conservation.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from talentflow.schemas import CandidateProfile, JobNeed, MatchResult, MatchStatus


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _json_load(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


class Repository:
    def __init__(self, database_url: str, embedding_dim: int = 512) -> None:
        self.url = database_url
        self.dim = embedding_dim
        self.pg = database_url.startswith(("postgres://", "postgresql://"))
        self._in_tx = False
        if self.pg:  # pragma: no cover - testé en CI avec le service Postgres
            import psycopg

            self._conn = psycopg.connect(database_url, autocommit=True)
        else:
            path = database_url.replace("sqlite:///", "", 1)
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------- bas niveau
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        if self.pg:  # pragma: no cover
            with self._conn.cursor() as cur:
                cur.execute(self._sql(sql), params)
        else:
            self._conn.execute(sql, params)
            if not self._in_tx:
                self._conn.commit()

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        if self.pg:  # pragma: no cover
            from psycopg.rows import dict_row

            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(self._sql(sql), params)
                rows = cur.fetchall()
        else:
            rows = [dict(r) for r in self._conn.execute(sql, params).fetchall()]
        return [{k: _iso(v) for k, v in r.items()} for r in rows]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.pg:  # pragma: no cover
            with self._conn.transaction():
                yield
        else:
            self._in_tx = True
            try:
                yield
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                self._in_tx = False

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- schéma
    def init_schema(self) -> None:
        # Horodatages ISO-8601 et JSON stockés en TEXT dans les deux moteurs (code SQL
        # identique) ; dbt les type (::timestamptz, ::jsonb) dans les modèles de staging.
        ts = "TEXT"
        js = "TEXT"
        vec = f"vector({self.dim})" if self.pg else "BLOB"
        stmts = []
        if self.pg:  # pragma: no cover
            stmts.append("CREATE EXTENSION IF NOT EXISTS vector")
        stmts += [
            f"""CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY, kind TEXT NOT NULL, filename TEXT,
                sha256 TEXT UNIQUE NOT NULL, source TEXT, sender TEXT, parser TEXT,
                pseudo_text TEXT, pii_counts {js}, received_at {ts} NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS pii_vault (
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                token TEXT NOT NULL, value TEXT NOT NULL, created_at {ts} NOT NULL,
                PRIMARY KEY (document_id, token))""",
            f"""CREATE TABLE IF NOT EXISTS candidates (
                candidate_id TEXT PRIMARY KEY,
                document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
                title TEXT, seniority TEXT, years_experience REAL, location TEXT,
                availability_date TEXT, daily_rate REAL, profile {js} NOT NULL,
                extractor TEXT, boond_id TEXT, created_at {ts} NOT NULL,
                updated_at {ts} NOT NULL, retention_until TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS candidate_embeddings (
                candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                embedding {vec} NOT NULL, model TEXT, updated_at {ts} NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS needs (
                need_id TEXT PRIMARY KEY,
                document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
                client TEXT, title TEXT, need {js} NOT NULL, status TEXT NOT NULL,
                received_at {ts} NOT NULL, matched_at {ts})""",
            f"""CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                need_id TEXT NOT NULL REFERENCES needs(need_id) ON DELETE CASCADE,
                candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
                rank INTEGER, final_score REAL, pre_score REAL, vector_score REAL,
                skill_coverage REAL, seniority_fit REAL, judge_score INTEGER,
                recommendation TEXT, detail {js}, status TEXT NOT NULL,
                created_at {ts} NOT NULL, reviewed_at {ts}, reviewer TEXT,
                review_comment TEXT, dossier_path TEXT, sent_at {ts})""",
            f"""CREATE TABLE IF NOT EXISTS prospection_emails (
                email_id TEXT PRIMARY KEY, prospect_company TEXT, contact_role TEXT,
                subject TEXT, body TEXT, profiles {js}, status TEXT NOT NULL,
                created_at {ts} NOT NULL, reviewed_at {ts}, sent_at {ts})""",
            f"""CREATE TABLE IF NOT EXISTS audit_log (
                event_id TEXT PRIMARY KEY, ts {ts} NOT NULL, actor TEXT, action TEXT,
                entity TEXT, entity_id TEXT, details {js})""",
            f"""CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY, run_type TEXT, started_at {ts} NOT NULL,
                finished_at {ts}, status TEXT, items INTEGER, duration_ms INTEGER,
                details {js})""",
            "CREATE INDEX IF NOT EXISTS ix_matches_need ON matches(need_id)",
            "CREATE INDEX IF NOT EXISTS ix_matches_status ON matches(status)",
            "CREATE INDEX IF NOT EXISTS ix_needs_status ON needs(status)",
        ]
        if self.pg:  # pragma: no cover
            stmts.append(
                "CREATE INDEX IF NOT EXISTS ix_emb_hnsw ON candidate_embeddings "
                "USING hnsw (embedding vector_cosine_ops)"
            )
        for s in stmts:
            self.execute(s)

    def reset(self) -> None:
        """Vide toutes les tables (démo uniquement)."""
        for table in ("matches", "candidate_embeddings", "candidates", "needs", "pii_vault",
                      "documents", "prospection_emails", "audit_log", "pipeline_runs"):
            self.execute(f"DELETE FROM {table}")

    # ------------------------------------------------------------- documents
    def find_document(self, sha256: str) -> dict | None:
        return self.one("SELECT * FROM documents WHERE sha256 = ?", (sha256,))

    def add_document(self, kind: str, filename: str, sha256: str, source: str,
                     pseudo_text: str, parser: str, pii_counts: dict,
                     sender: str | None = None) -> str:
        doc_id = new_id("DOC")
        self.execute(
            "INSERT INTO documents (document_id, kind, filename, sha256, source, sender, parser,"
            " pseudo_text, pii_counts, received_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc_id, kind, filename, sha256, source, sender, parser, pseudo_text,
             json.dumps(pii_counts), utcnow()),
        )
        return doc_id

    def save_vault(self, document_id: str, vault: dict[str, str]) -> None:
        now = utcnow()
        for token, value in vault.items():
            self.execute(
                "INSERT INTO pii_vault (document_id, token, value, created_at) VALUES (?,?,?,?)",
                (document_id, token, value, now),
            )

    def get_vault(self, document_id: str) -> dict[str, str]:
        rows = self.query("SELECT token, value FROM pii_vault WHERE document_id = ?", (document_id,))
        return {r["token"]: r["value"] for r in rows}

    # ------------------------------------------------------------ candidats
    def save_candidate(self, profile: CandidateProfile, document_id: str | None,
                       extractor: str, retention_days: int, boond_id: str | None = None) -> str:
        cid = profile.candidate_id or new_id("CAND")
        profile.candidate_id = cid
        now = utcnow()
        retention = (date.today() + timedelta(days=retention_days)).isoformat()
        self.execute("DELETE FROM candidates WHERE candidate_id = ?", (cid,))
        self.execute(
            "INSERT INTO candidates (candidate_id, document_id, title, seniority, years_experience,"
            " location, availability_date, daily_rate, profile, extractor, boond_id, created_at,"
            " updated_at, retention_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, document_id, profile.title, profile.seniority.value, profile.years_experience,
             profile.location,
             profile.availability_date.isoformat() if profile.availability_date else None,
             profile.daily_rate, profile.model_dump_json(), extractor, boond_id, now, now,
             retention),
        )
        return cid

    def get_candidate(self, candidate_id: str) -> CandidateProfile | None:
        row = self.one("SELECT profile FROM candidates WHERE candidate_id = ?", (candidate_id,))
        return CandidateProfile.model_validate(_json_load(row["profile"])) if row else None

    def get_candidate_row(self, candidate_id: str) -> dict | None:
        return self.one("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,))

    def list_candidates(self) -> list[CandidateProfile]:
        rows = self.query("SELECT profile FROM candidates ORDER BY candidate_id")
        return [CandidateProfile.model_validate(_json_load(r["profile"])) for r in rows]

    def count(self, table: str) -> int:
        assert table in {"candidates", "needs", "matches", "documents", "prospection_emails"}
        return int(self.one(f"SELECT COUNT(*) AS n FROM {table}")["n"])

    # ----------------------------------------------------------- embeddings
    def save_embedding(self, candidate_id: str, vector: np.ndarray, model: str) -> None:
        self.execute("DELETE FROM candidate_embeddings WHERE candidate_id = ?", (candidate_id,))
        if self.pg:  # pragma: no cover
            value: Any = "[" + ",".join(f"{x:.6f}" for x in vector.tolist()) + "]"
            sql = ("INSERT INTO candidate_embeddings (candidate_id, embedding, model, updated_at)"
                   " VALUES (?, ?::vector, ?, ?)")
        else:
            value = np.asarray(vector, dtype=np.float32).tobytes()
            sql = ("INSERT INTO candidate_embeddings (candidate_id, embedding, model, updated_at)"
                   " VALUES (?, ?, ?, ?)")
        self.execute(sql, (candidate_id, value, model, utcnow()))

    def vector_search(self, query: np.ndarray, k: int) -> list[tuple[str, float]]:
        """Top-k (candidate_id, similarité cosinus)."""
        if self.pg:  # pragma: no cover
            q = "[" + ",".join(f"{x:.6f}" for x in query.tolist()) + "]"
            rows = self.query(
                "SELECT candidate_id, 1 - (embedding <=> ?::vector) AS score "
                "FROM candidate_embeddings ORDER BY embedding <=> ?::vector LIMIT ?",
                (q, q, k),
            )
            return [(r["candidate_id"], float(r["score"])) for r in rows]
        rows = self.query("SELECT candidate_id, embedding FROM candidate_embeddings")
        if not rows:
            return []
        ids = [r["candidate_id"] for r in rows]
        mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        scores = mat @ np.asarray(query, dtype=np.float32)
        order = np.argsort(-scores)[:k]
        return [(ids[i], float(scores[i])) for i in order]

    # ---------------------------------------------------------------- besoins
    def save_need(self, need: JobNeed, document_id: str | None) -> str:
        nid = need.need_id or new_id("NEED")
        need.need_id = nid
        self.execute("DELETE FROM needs WHERE need_id = ?", (nid,))
        self.execute(
            "INSERT INTO needs (need_id, document_id, client, title, need, status, received_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (nid, document_id, need.client, need.title, need.model_dump_json(), "received",
             utcnow()),
        )
        return nid

    def get_need(self, need_id: str) -> JobNeed | None:
        row = self.one("SELECT need FROM needs WHERE need_id = ?", (need_id,))
        return JobNeed.model_validate(_json_load(row["need"])) if row else None

    def list_needs(self, status: str | None = None) -> list[dict]:
        sql = "SELECT need_id, client, title, status, received_at, matched_at FROM needs"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        return self.query(sql + " ORDER BY received_at DESC", params)

    def set_need_status(self, need_id: str, status: str) -> None:
        extra = ", matched_at = ?" if status == "matched" else ""
        params = (status, utcnow(), need_id) if extra else (status, need_id)
        self.execute(f"UPDATE needs SET status = ?{extra} WHERE need_id = ?", params)

    # --------------------------------------------------------------- matches
    def save_matches(self, need_id: str, results: list[MatchResult]) -> list[str]:
        """Remplace les propositions en attente du besoin (les décisions passées restent)."""
        self.execute("DELETE FROM matches WHERE need_id = ? AND status = ?",
                     (need_id, MatchStatus.PENDING.value))
        decided = {r["candidate_id"] for r in self.query(
            "SELECT candidate_id FROM matches WHERE need_id = ?", (need_id,))}
        ids = []
        now = utcnow()
        for r in results:
            if r.candidate_id in decided:
                continue
            mid = new_id("M")
            detail = r.model_dump(mode="json", include={"matched_skills", "missing_skills",
                                                        "filters", "judge"})
            self.execute(
                "INSERT INTO matches (match_id, need_id, candidate_id, rank, final_score,"
                " pre_score, vector_score, skill_coverage, seniority_fit, judge_score,"
                " recommendation, detail, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, need_id, r.candidate_id, r.rank, r.final_score, r.pre_score,
                 r.vector_score, r.skill_coverage, r.seniority_fit,
                 r.judge.score if r.judge else None,
                 r.judge.recommendation if r.judge else None, json.dumps(detail),
                 MatchStatus.PENDING.value, now),
            )
            ids.append(mid)
        return ids

    def list_matches(self, need_id: str | None = None, status: str | None = None) -> list[dict]:
        sql = ("SELECT m.*, c.title AS candidate_title, c.seniority, c.years_experience,"
               " n.title AS need_title, n.client FROM matches m"
               " JOIN candidates c ON c.candidate_id = m.candidate_id"
               " JOIN needs n ON n.need_id = m.need_id WHERE 1=1")
        params: list = []
        if need_id:
            sql += " AND m.need_id = ?"
            params.append(need_id)
        if status:
            sql += " AND m.status = ?"
            params.append(status)
        rows = self.query(sql + " ORDER BY m.need_id, m.rank", params)
        for r in rows:
            r["detail"] = _json_load(r["detail"])
        return rows

    def get_match(self, match_id: str) -> dict | None:
        rows = [r for r in self.list_matches() if r["match_id"] == match_id]
        return rows[0] if rows else None

    def update_match(self, match_id: str, **fields: Any) -> None:
        allowed = {"status", "reviewed_at", "reviewer", "review_comment", "dossier_path", "sent_at"}
        keys = [k for k in fields if k in allowed]
        if not keys:
            return
        sets = ", ".join(f"{k} = ?" for k in keys)
        self.execute(f"UPDATE matches SET {sets} WHERE match_id = ?",
                     [fields[k] for k in keys] + [match_id])

    # ------------------------------------------------------------ prospection
    def save_prospection(self, email: dict) -> str:
        eid = new_id("PROS")
        self.execute(
            "INSERT INTO prospection_emails (email_id, prospect_company, contact_role, subject,"
            " body, profiles, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (eid, email["prospect_company"], email.get("contact_role", ""), email["subject"],
             email["body"], json.dumps(email.get("highlighted_profiles", [])),
             email.get("status", "draft"), utcnow()),
        )
        return eid

    def list_prospection(self, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM prospection_emails", ()
        if status:
            sql, params = sql + " WHERE status = ?", (status,)
        rows = self.query(sql + " ORDER BY created_at DESC", params)
        for r in rows:
            r["profiles"] = _json_load(r["profiles"])
        return rows

    def update_prospection(self, email_id: str, status: str) -> None:
        col = "sent_at" if status == "sent" else "reviewed_at"
        self.execute(f"UPDATE prospection_emails SET status = ?, {col} = ? WHERE email_id = ?",
                     (status, utcnow(), email_id))

    # ------------------------------------------------------------ audit / runs
    def audit(self, actor: str, action: str, entity: str, entity_id: str,
              details: dict | None = None) -> None:
        self.execute(
            "INSERT INTO audit_log (event_id, ts, actor, action, entity, entity_id, details)"
            " VALUES (?,?,?,?,?,?,?)",
            (new_id("EVT"), utcnow(), actor, action, entity, entity_id,
             json.dumps(details or {}, ensure_ascii=False)),
        )

    def list_audit(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))

    def start_run(self, run_type: str) -> tuple[str, datetime]:
        rid = new_id("RUN")
        started = datetime.now(timezone.utc)
        self.execute("INSERT INTO pipeline_runs (run_id, run_type, started_at, status)"
                     " VALUES (?,?,?,?)", (rid, run_type, started.isoformat(), "running"))
        return rid, started

    def finish_run(self, run_id: str, started: datetime, status: str, items: int,
                   details: dict | None = None) -> None:
        end = datetime.now(timezone.utc)
        self.execute(
            "UPDATE pipeline_runs SET finished_at = ?, status = ?, items = ?, duration_ms = ?,"
            " details = ? WHERE run_id = ?",
            (end.isoformat(), status, items, int((end - started).total_seconds() * 1000),
             json.dumps(details or {}, ensure_ascii=False), run_id),
        )

    # --------------------------------------------------------------- RGPD
    def erase_candidate(self, candidate_id: str, actor: str, reason: str) -> bool:
        """Droit à l'effacement (art. 17) : profil, vecteur, propositions, coffre, document."""
        row = self.get_candidate_row(candidate_id)
        if not row:
            return False
        with self.transaction():
            self.execute("DELETE FROM candidate_embeddings WHERE candidate_id = ?", (candidate_id,))
            self.execute("DELETE FROM matches WHERE candidate_id = ?", (candidate_id,))
            self.execute("DELETE FROM candidates WHERE candidate_id = ?", (candidate_id,))
            if row.get("document_id"):
                self.execute("DELETE FROM pii_vault WHERE document_id = ?", (row["document_id"],))
                self.execute("DELETE FROM documents WHERE document_id = ?", (row["document_id"],))
        self.audit(actor, "gdpr_erase", "candidate", candidate_id, {"reason": reason})
        return True

    def expired_candidates(self, today: date | None = None) -> list[str]:
        today = today or date.today()
        rows = self.query("SELECT candidate_id FROM candidates WHERE retention_until < ?",
                          (today.isoformat(),))
        return [r["candidate_id"] for r in rows]
