"""Orchestration de la chaîne TalentFlow (utilisée par l'API, la CLI, n8n et Airflow)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from talentflow.config import Settings, get_settings
from talentflow.embeddings.embedders import Embedder, build_embedder
from talentflow.extraction.extractor import Extractor, build_extractor
from talentflow.generation.dossier import build_dossier, safe_filename
from talentflow.generation.market import (
    gap_analysis,
    load_job_posts,
    pool_supply,
    skill_demand,
    trends,
)
from talentflow.generation.prospection import ProspectionWriter, load_prospects
from talentflow.ingestion.parsers import parse_bytes
from talentflow.ingestion.sources import DocKind, classify
from talentflow.llm.clients import LLMClient, build_llm
from talentflow.matching.engine import MatchingEngine, MatchReport, Mode
from talentflow.matching.judge import build_judge
from talentflow.privacy.pseudonymizer import Pseudonymizer, initials, mask_with_vault
from talentflow.schemas import JudgeVerdict, MatchStatus
from talentflow.storage.repository import Repository

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    kind: DocKind
    entity_id: str | None
    document_id: str | None
    duplicate: bool = False
    pii_counts: dict = field(default_factory=dict)
    removed: dict = field(default_factory=dict)


class TalentFlow:
    def __init__(self, settings: Settings, repo: Repository, extractor: Extractor,
                 embedder: Embedder, engine: MatchingEngine, pseudonymizer: Pseudonymizer,
                 llm: LLMClient | None = None, today: date | None = None) -> None:
        self.settings = settings
        self.repo = repo
        self.extractor = extractor
        self.embedder = embedder
        self.engine = engine
        self.pseudonymizer = pseudonymizer
        self.llm = llm
        self.today = today or date.today()

    # ---------------------------------------------------------------- build
    @classmethod
    def build(cls, settings: Settings | None = None, today: date | None = None) -> TalentFlow:
        settings = settings or get_settings()
        repo = Repository(settings.database_url, embedding_dim=settings.embedding_dim)
        repo.init_schema()
        llm = build_llm(settings)
        today = today or date.today()
        engine = MatchingEngine(
            judge=build_judge(llm),
            judge_top_n=settings.judge_top_n,
            proposal_top_k=settings.proposal_top_k,
            judge_weight=settings.judge_weight,
            rate_tolerance=settings.rate_tolerance,
            availability_tolerance_days=settings.availability_tolerance_days,
            today=today,
        )
        return cls(settings, repo, build_extractor(llm, today=today), build_embedder(settings),
                   engine, Pseudonymizer(use_presidio=settings.use_presidio), llm, today)

    # ------------------------------------------------------------ ingestion
    def ingest(self, data: bytes, filename: str, kind: DocKind | None = None,
               source: str = "api", sender: str | None = None,
               known_names: list[str] | None = None, actor: str = "system") -> IngestResult:
        parsed = parse_bytes(data, filename)
        kind = kind or classify(filename, text_preview=parsed.text[:3000])

        existing = self.repo.find_document(parsed.sha256)
        if existing:
            table = "candidates" if existing["kind"] == "cv" else "needs"
            col = "candidate_id" if existing["kind"] == "cv" else "need_id"
            row = self.repo.one(f"SELECT {col} AS id FROM {table} WHERE document_id = ?",
                                (existing["document_id"],))
            return IngestResult(kind=existing["kind"], entity_id=row["id"] if row else None,
                                document_id=existing["document_id"], duplicate=True)

        # Les fiches de besoin contiennent aussi des données personnelles (nom du
        # contact client, téléphone) : même traitement que les CV.
        pseudo = self.pseudonymizer.pseudonymize(parsed.text, known_names=known_names)
        safe_name = mask_with_vault(filename, pseudo.vault)
        source = mask_with_vault(source, pseudo.vault)
        doc_id = self.repo.add_document(kind, safe_name, parsed.sha256, source, pseudo.text,
                                        parsed.parser, pseudo.entity_counts, sender)
        self.repo.save_vault(doc_id, pseudo.vault)

        if kind == "cv":
            profile = self.extractor.extract_cv(pseudo.text)
            entity_id = self.repo.save_candidate(profile, doc_id, self.extractor.name,
                                                 self.settings.retention_days)
            self._embed(entity_id, profile.embedding_text())
        else:
            need = self.extractor.extract_need(pseudo.text)
            entity_id = self.repo.save_need(need, doc_id)

        self.repo.audit(actor, f"ingest_{kind}", "cv" if kind == "cv" else "need", entity_id,
                        {"filename": safe_name, "source": source, "parser": parsed.parser,
                         "pii_masked": pseudo.entity_counts, "pii_removed": pseudo.removed})
        return IngestResult(kind=kind, entity_id=entity_id, document_id=doc_id,
                            pii_counts=pseudo.entity_counts, removed=pseudo.removed)

    def ingest_path(self, path: str | Path, kind: DocKind | None = None, **kw) -> IngestResult:
        p = Path(path)
        return self.ingest(p.read_bytes(), p.name, kind=kind, source=f"folder:{p}", **kw)

    def _embed(self, candidate_id: str, text: str) -> None:
        vec = self.embedder.embed_one(text)
        self.repo.save_embedding(candidate_id, vec, self.settings.embedding_provider)

    def reembed_all(self) -> int:
        candidates = self.repo.list_candidates()
        if not candidates:
            return 0
        vecs = self.embedder.embed([c.embedding_text() for c in candidates])
        for c, v in zip(candidates, vecs):
            self.repo.save_embedding(c.candidate_id, v, self.settings.embedding_provider)
        return len(candidates)

    # -------------------------------------------------------------- matching
    def match_need(self, need_id: str, mode: Mode = "full", persist: bool = True,
                   actor: str = "system") -> MatchReport:
        need = self.repo.get_need(need_id)
        if not need:
            raise KeyError(f"Besoin inconnu : {need_id}")
        qvec = self.embedder.embed_one(need.embedding_text())
        hits = self.repo.vector_search(qvec, self.settings.retrieval_k)
        retrieved = []
        for cid, score in hits:
            profile = self.repo.get_candidate(cid)
            if profile:
                retrieved.append((profile, score))
        report = self.engine.match(need, retrieved, mode=mode)
        if persist:
            self.repo.save_matches(need_id, report.proposals)
            self.repo.set_need_status(need_id, "matched")
            self.repo.audit(actor, "match_need", "need", need_id, {
                "mode": mode, "retrieved": report.retrieved,
                "eligible": len(report.ranked), "excluded": len(report.excluded),
                "proposed": [r.candidate_id for r in report.proposals],
            })
        return report

    # ------------------------------------------------------------- dossiers
    def display_name(self, candidate_id: str) -> str:
        row = self.repo.get_candidate_row(candidate_id)
        vault = self.repo.get_vault(row["document_id"]) if row and row.get("document_id") else {}
        full = vault.get("[PERSONNE_1]")
        if not full:
            return f"Consultant {candidate_id[-4:]}"
        return initials(full) if self.settings.anonymize_dossiers else full

    def generate_dossier(self, match_id: str, actor: str = "system",
                         allow_unvalidated: bool = False) -> Path:
        match = self.repo.get_match(match_id)
        if not match:
            raise KeyError(f"Proposition inconnue : {match_id}")
        if match["status"] not in {MatchStatus.VALIDATED.value, MatchStatus.SENT.value} \
                and not allow_unvalidated:
            raise PermissionError("Le dossier n'est généré qu'après validation humaine")
        profile = self.repo.get_candidate(match["candidate_id"])
        need = self.repo.get_need(match["need_id"])
        judge = match["detail"].get("judge")
        verdict = JudgeVerdict.model_validate(judge) if judge else None
        name = self.display_name(match["candidate_id"])
        fname = (f"Dossier_{safe_filename(profile.title)}_{safe_filename(name)}_"
                 f"{match['need_id']}.docx")
        out = build_dossier(
            profile, need, name, self.settings.output_dir / "dossiers" / fname,
            verdict=verdict, brand=self.settings.brand_name,
            validated_by=match.get("reviewer"),
        )
        self.repo.update_match(match_id, dossier_path=str(out))
        self.repo.audit(actor, "generate_dossier", "match", match_id, {"path": str(out)})
        return out

    # ------------------------------------------------------- veille / prospection
    def market_report(self, posts_path: str | Path | None = None) -> dict:
        posts = load_job_posts(posts_path or self.settings.data_dir / "market" / "job_posts.csv")
        needs = [self.repo.get_need(n["need_id"]) for n in self.repo.list_needs()]
        needs = [n for n in needs if n]
        demand = skill_demand(needs, posts)
        supply = pool_supply(self.repo.list_candidates(), self.today)
        return {"gaps": gap_analysis(demand, supply), "trends": trends(posts, self.today),
                "job_posts": len(posts), "needs": len(needs)}

    def generate_prospection(self, posts_path: str | Path | None = None,
                             prospects_path: str | Path | None = None,
                             actor: str = "system") -> list[str]:
        base = self.settings.data_dir / "market"
        posts = load_job_posts(posts_path or base / "job_posts.csv")
        prospects = load_prospects(prospects_path or base / "prospects.csv")
        writer = ProspectionWriter(self.llm, sender=f"L'équipe {self.settings.brand_name}")
        candidates = self.repo.list_candidates()
        ids = []
        for prospect in prospects:
            email = writer.write(prospect, posts, candidates)
            if email:
                ids.append(self.repo.save_prospection(email.model_dump()))
        self.repo.audit(actor, "generate_prospection", "campaign", f"{len(ids)} emails",
                        {"prospects": len(prospects)})
        return ids

    # ---------------------------------------------------------------- RGPD
    def purge_expired(self, actor: str = "retention-job") -> list[str]:
        erased = []
        for cid in self.repo.expired_candidates(self.today):
            if self.repo.erase_candidate(cid, actor, "durée de conservation expirée"):
                erased.append(cid)
        return erased
