"""Interface en ligne de commande.

    talentflow generate-data          # données synthétiques (CV, besoins, marché)
    talentflow demo                   # démo complète hors-ligne de bout en bout
    talentflow ingest <fichier|dossier> [--kind cv|need]
    talentflow match <NEED-ID> | --all
    talentflow eval                   # rapport d'évaluation (reports/evaluation.md)
    talentflow report                 # indicateurs hebdomadaires
    talentflow market | prospection | purge | sync-boond
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from talentflow.config import get_settings


def _flow(today: date | None = None):
    from talentflow.pipeline import TalentFlow

    return TalentFlow.build(get_settings(), today=today)


def cmd_generate(args) -> None:
    from talentflow.evaluation.synthetic import generate

    print(json.dumps(generate(args.out, args.consultants, args.needs, args.seed),
                     ensure_ascii=False, indent=2))


def cmd_ingest(args) -> None:
    flow = _flow()
    target = Path(args.path)
    paths = sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else [target]
    for p in paths:
        try:
            res = flow.ingest_path(p, kind=args.kind, actor="cli")
        except ValueError as exc:
            print(f"✗ {p.name} : {exc}")
            continue
        tag = "doublon" if res.duplicate else res.kind
        print(f"✓ {p.name} → {res.entity_id} ({tag}) PII masquées : {res.pii_counts}")


def cmd_match(args) -> None:
    flow = _flow()
    ids = [n["need_id"] for n in flow.repo.list_needs()] if args.all else [args.need_id]
    for nid in ids:
        report = flow.match_need(nid, mode=args.mode, actor="cli")
        need = flow.repo.get_need(nid)
        print(f"\n■ {nid} · {need.title} — {report.retrieved} analysés, "
              f"{len(report.ranked)} éligibles, {len(report.excluded)} exclus")
        for r in report.proposals:
            j = r.judge
            print(f"  #{r.rank} {r.candidate_id}  score={r.final_score:.2f}"
                  + (f"  juge={j.score} ({j.recommendation})\n     {j.justification}" if j else ""))
        if args.explain:
            print(f"  Besoin structuré : lieu={need.location!r} télétravail={need.remote_policy.value} "
                  f"démarrage={need.start_date} TJM max={need.max_daily_rate} "
                  f"séniorité={need.seniority.value if need.seniority else None} "
                  f"langues={need.languages}")
            for r in report.excluded[:8]:
                c = flow.repo.get_candidate(r.candidate_id)
                print(f"  ✗ {r.candidate_id} ({c.title}, {c.location}, dispo {c.availability_date}, "
                      f"TJM {c.daily_rate}, langues {c.languages}) : {' ; '.join(r.filters.reasons)}")


def cmd_eval(args) -> None:
    from talentflow.evaluation.evaluate import run_evaluation

    settings = get_settings()
    res = run_evaluation(args.data, settings, args.out)
    print(Path(args.out, "evaluation.md").read_text(encoding="utf-8"))
    if args.min_precision and (res["matching"]["full"]["precision_at_3"] or 0) < args.min_precision:
        sys.exit(f"Précision@3 sous le seuil {args.min_precision} : régression détectée")
    if res["privacy"]["leaks"]:
        sys.exit("Fuite de données personnelles détectée")


def cmd_report(args) -> None:
    from talentflow.storage.metrics import weekly_kpis

    print(json.dumps(weekly_kpis(_flow().repo, args.weeks), ensure_ascii=False, indent=2))


def cmd_market(args) -> None:
    print(json.dumps(_flow().market_report(), ensure_ascii=False, indent=2))


def cmd_prospection(args) -> None:
    flow = _flow()
    ids = flow.generate_prospection(actor="cli")
    print(f"{len(ids)} brouillon(s) de prospection créés (à approuver dans l'interface).")


def cmd_purge(args) -> None:
    print(json.dumps({"erased": _flow().purge_expired()}, ensure_ascii=False))


def cmd_sync_boond(args) -> None:
    from talentflow.connectors.boond import BoondClient, resource_to_text

    s = get_settings()
    flow = _flow()
    client = BoondClient(s.boond_base_url or f"{s.api_base_url}/mock/boond",
                         s.boond_client_token, s.boond_user_token, s.boond_client_key)
    n = 0
    for res in client.list_resources():
        text = resource_to_text(res).encode("utf-8")
        out = flow.ingest(text, f"boond_{res['id']}.txt", kind="cv", source="boond",
                          actor="sync-boond")
        n += 0 if out.duplicate else 1
    print(f"{n} ressource(s) Boond importée(s).")


def cmd_demo(args) -> None:
    """Démo hors-ligne : données → ingestion → matching → validation → dossiers → KPI."""
    from talentflow.evaluation.synthetic import generate
    from talentflow.schemas import MatchStatus
    from talentflow.storage.metrics import weekly_kpis
    from talentflow.workflow.validation import transition

    settings = get_settings()
    db = settings.sqlite_path
    if not settings.is_postgres and db.exists():
        db.unlink()
    for old in (settings.output_dir / "dossiers").glob("*.docx"):
        old.unlink()
    print("1/6 Génération des données synthétiques…")
    stats = generate(settings.data_dir, n_consultants=args.consultants, n_needs=args.needs,
                     seed=args.seed)
    truth = json.loads((settings.data_dir / "synthetic" / "ground_truth.json").read_text("utf-8"))
    today = date.fromisoformat(truth["reference_date"])
    flow = _flow(today)

    print(f"2/6 Ingestion de {stats['consultants']} CV et {stats['needs']} besoins "
          "(pseudonymisation → extraction → embeddings)…")
    for p in sorted((settings.data_dir / "synthetic" / "cvs").iterdir()):
        flow.ingest_path(p, kind="cv", actor="demo")
    need_ids = []
    for p in sorted((settings.data_dir / "synthetic" / "needs").iterdir()):
        need_ids.append(flow.ingest_path(p, kind="need", actor="demo").entity_id)

    print("3/6 Matching des besoins…")
    for nid in need_ids:
        flow.match_need(nid, actor="demo")

    print("4/6 Validation humaine simulée (meilleur profil validé, profils « à étudier » rejetés)…")
    dossiers = []
    seen_needs: set[str] = set()
    for m in flow.repo.list_matches(status=MatchStatus.PENDING.value):  # triés par rang
        rec = (m["detail"].get("judge") or {}).get("recommendation")
        if m["need_id"] not in seen_needs:
            seen_needs.add(m["need_id"])
            transition(flow.repo, m["match_id"], MatchStatus.VALIDATED, "demo@esn.fr")
            dossiers.append(flow.generate_dossier(m["match_id"], actor="demo@esn.fr"))
        elif rec == "a_etudier":
            transition(flow.repo, m["match_id"], MatchStatus.REJECTED, "demo@esn.fr",
                       "compétences obligatoires incomplètes")

    print("5/6 Veille marché et brouillons de prospection…")
    market = flow.market_report()
    pros = flow.generate_prospection(actor="demo")

    print("6/6 Indicateurs de la semaine :")
    print(json.dumps(weekly_kpis(flow.repo, 1), ensure_ascii=False, indent=2))
    print(f"\n✓ {len(dossiers)} dossiers Word dans {settings.output_dir / 'dossiers'}")
    print(f"✓ {len(pros)} emails de prospection en brouillon")
    to_source = [g["skill"] for g in market["gaps"] if g["action"] == "à sourcer"]
    print("✓ Compétences à sourcer en priorité : " + (", ".join(to_source) or "aucune"))
    print("\nÉtape suivante : `make eval` pour le rapport d'évaluation, "
          "`make up` pour l'API, l'interface et n8n.")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="talentflow", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate-data")
    g.add_argument("--out", default="data")
    g.add_argument("--consultants", type=int, default=60)
    g.add_argument("--needs", type=int, default=15)
    g.add_argument("--seed", type=int, default=42)
    g.set_defaults(func=cmd_generate)

    i = sub.add_parser("ingest")
    i.add_argument("path")
    i.add_argument("--kind", choices=["cv", "need"])
    i.set_defaults(func=cmd_ingest)

    m = sub.add_parser("match")
    m.add_argument("need_id", nargs="?")
    m.add_argument("--all", action="store_true")
    m.add_argument("--mode", choices=["vector", "rules", "full"], default="full")
    m.add_argument("--explain", action="store_true",
                   help="affiche le besoin structuré et les motifs d'exclusion")
    m.set_defaults(func=cmd_match)

    e = sub.add_parser("eval")
    e.add_argument("--data", default="data")
    e.add_argument("--out", default="reports")
    e.add_argument("--min-precision", type=float, default=0.0)
    e.set_defaults(func=cmd_eval)

    r = sub.add_parser("report")
    r.add_argument("--weeks", type=int, default=8)
    r.set_defaults(func=cmd_report)

    for name, fn in (("market", cmd_market), ("prospection", cmd_prospection),
                     ("purge", cmd_purge), ("sync-boond", cmd_sync_boond)):
        sub.add_parser(name).set_defaults(func=fn)

    d = sub.add_parser("demo")
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--consultants", type=int, default=60,
                   help="taille du vivier (réduire avec un LLM gratuit à débit limité)")
    d.add_argument("--needs", type=int, default=15)
    d.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    if args.cmd == "match" and not (args.all or args.need_id):
        parser.error("indiquez un NEED-ID ou --all")
    args.func(args)


if __name__ == "__main__":
    main()
