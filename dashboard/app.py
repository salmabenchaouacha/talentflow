"""Interface commerciale TalentFlow (Streamlit) : validation, pilotage, veille, prospection.

Lancement : streamlit run dashboard/app.py  (l'API doit tourner sur TF_API_BASE_URL)
"""

from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st

API = os.environ.get("TF_API_BASE_URL", "http://localhost:8000")
SENIORITY = {"junior": "junior", "confirme": "confirmé", "senior": "senior", "expert": "expert"}
KEY = os.environ.get("TF_API_KEY", "change-me")
HEADERS = {"X-API-Key": KEY}

st.set_page_config(page_title="TalentFlow", page_icon="🧭", layout="wide")


def api(method: str, path: str, **kw):
    resp = requests.request(method, f"{API}{path}", headers=HEADERS, timeout=120, **kw)
    if resp.status_code >= 400:
        st.error(f"{resp.status_code} : {resp.text[:300]}")
        return None
    return resp.json() if "json" in resp.headers.get("content-type", "") else resp.content


st.sidebar.title("🧭 TalentFlow")
st.sidebar.caption("Du besoin client au profil validé")
reviewer = st.sidebar.text_input("Votre nom (traçabilité)", value="commercial@esn.fr")
page = st.sidebar.radio(
    "Navigation",
    ["Propositions à valider", "Tableau de bord hebdo", "Veille marché", "Prospection",
     "Journal & RGPD"],
)

# =================================================================== Validation
if page == "Propositions à valider":
    st.header("Propositions à valider")
    needs = api("GET", "/needs") or []
    if not needs:
        st.info("Aucun besoin reçu pour l'instant. Envoyez une fiche de besoin par email ou "
                "via POST /needs.")
        st.stop()
    labels = {f"{n['need_id']} · {n['title']} ({n.get('client') or 'client ?'})": n["need_id"]
              for n in needs}
    default = st.query_params.get("need_id")
    keys = list(labels)
    idx = next((i for i, k in enumerate(keys) if labels[k] == default), 0)
    need_id = labels[st.selectbox("Besoin", keys, index=idx)]
    need = api("GET", f"/needs/{need_id}") or {}

    with st.expander("Fiche de besoin structurée", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("Séniorité", need.get("seniority") or "—")
        c2.metric("Expérience min.", f"{need.get('min_years', 0):g} ans")
        c3.metric("TJM max", f"{need.get('max_daily_rate') or '—'} €")
        st.write("**Obligatoires :**", ", ".join(need.get("required_skills", [])))
        st.write("**Souhaitées :**", ", ".join(need.get("nice_to_have", [])))
        st.write("**Lieu :**", need.get("location"), "·", need.get("remote_policy"),
                 "· Démarrage :", need.get("start_date"))

    if st.button("🔄 Relancer le matching"):
        res = api("POST", f"/needs/{need_id}/match")
        if res:
            st.success(f"{len(res['proposals'])} profil(s) proposé(s) sur "
                       f"{res['eligible']} éligibles ({res['retrieved']} analysés).")

    matches = api("GET", "/matches", params={"need_id": need_id}) or []
    if not matches:
        st.warning("Pas encore de proposition pour ce besoin : lancez le matching.")
    for m in matches:
        d = m["detail"] or {}
        judge = d.get("judge") or {}
        status = m["status"]
        badge = {"pending_review": "🟡 À valider", "validated": "🟢 Validé",
                 "rejected": "🔴 Rejeté", "sent": "📤 Envoyé"}[status]
        with st.container(border=True):
            top = st.columns([3, 1, 1, 1])
            top[0].subheader(f"#{m['rank']} · {m['candidate_title']} "
                             f"({SENIORITY.get(m['seniority'], m['seniority'])}, "
                             f"{m['years_experience']:g} ans)")
            top[1].metric("Score final", f"{m['final_score'] * 100:.0f}")
            top[2].metric("Juge IA", judge.get("score", "—"))
            top[3].markdown(f"### {badge}")
            st.write(judge.get("justification", ""))
            cols = st.columns(2)
            cols[0].markdown("**Points forts**\n" + "\n".join(
                f"- {s}" for s in judge.get("strengths", [])))
            cols[1].markdown("**Points d'attention**\n" + "\n".join(
                f"- {s}" for s in judge.get("gaps", []) + d.get("filters", {}).get("warnings", [])))
            st.caption(f"Compétences couvertes : {', '.join(d.get('matched_skills', []))} · "
                       f"manquantes : {', '.join(d.get('missing_skills', [])) or 'aucune'} · "
                       f"similarité {m['vector_score']:.2f} · couverture {m['skill_coverage']:.0%}")

            if status == "pending_review":
                b1, b2, b3 = st.columns([1, 2, 1])
                if b1.button("✅ Valider", key=f"v{m['match_id']}"):
                    res = api("POST", f"/matches/{m['match_id']}/validate",
                              json={"reviewer": reviewer, "comment": ""})
                    if res:
                        st.success("Validé : dossier généré"
                                   + (" et n8n notifié." if res.get("n8n_notified") else "."))
                        st.rerun()
                motive = b2.text_input("Motif de rejet", key=f"c{m['match_id']}",
                                       placeholder="ex. expérience secteur bancaire manquante")
                if b3.button("❌ Rejeter", key=f"r{m['match_id']}"):
                    if api("POST", f"/matches/{m['match_id']}/reject",
                           json={"reviewer": reviewer, "comment": motive}):
                        st.rerun()
            elif status in {"validated", "sent"}:
                content = api("GET", f"/matches/{m['match_id']}/dossier")
                if content:
                    st.download_button("📄 Télécharger le dossier Word", content,
                                       file_name=f"dossier_{m['match_id']}.docx",
                                       key=f"d{m['match_id']}")

# ================================================================== Dashboard
elif page == "Tableau de bord hebdo":
    st.header("Pilotage hebdomadaire")
    data = api("GET", "/metrics/weekly") or {}
    weeks = pd.DataFrame(data.get("weeks", []))
    if weeks.empty:
        st.info("Pas encore de données.")
        st.stop()
    last = weeks.iloc[-1]
    k = st.columns(5)
    k[0].metric("Besoins traités", int(last["needs_received"]))
    k[1].metric("CV analysés", int(last["cvs_ingested"]))
    k[2].metric("Propositions", int(last["proposals"]))
    k[3].metric("Taux de validation",
                f"{last['validation_rate']:.0%}" if pd.notna(last["validation_rate"]) else "—")
    k[4].metric("Délai médian besoin → proposition",
                f"{last['median_minutes_to_proposal']:.0f} min"
                if pd.notna(last["median_minutes_to_proposal"]) else "—")
    st.caption("Semaine " + last["week"] + ". Temps gagné estimé : "
               f"{last['estimated_hours_saved']} h (hypothèse : 2 h de travail manuel par besoin "
               "et 30 min par dossier, à mesurer avec l'équipe).")
    c1, c2 = st.columns(2)
    c1.subheader("Volume par semaine")
    c1.bar_chart(weeks.set_index("week")[["needs_received", "proposals", "validated"]])
    c2.subheader("Qualité")
    c2.line_chart(weeks.set_index("week")[["validation_rate", "avg_final_score"]])
    pool = data.get("pool", {})
    st.subheader(f"Vivier : {pool.get('candidates', 0)} consultants")
    p1, p2 = st.columns(2)
    p1.bar_chart(pd.Series(pool.get("by_seniority", {}), name="consultants"))
    p2.dataframe(pd.DataFrame(pool.get("top_skills", []), columns=["Compétence", "Consultants"]),
                 hide_index=True, use_container_width=True)
    if data.get("rejection_reasons"):
        st.subheader("Motifs de rejet (à réinjecter dans le matching)")
        st.dataframe(pd.DataFrame(data["rejection_reasons"], columns=["Motif", "Nombre"]),
                     hide_index=True)

# ===================================================================== Marché
elif page == "Veille marché":
    st.header("Veille marché : demande vs vivier")
    data = api("GET", "/market") or {}
    st.caption(f"{data.get('needs', 0)} besoins clients + {data.get('job_posts', 0)} offres "
               "publiques analysés.")
    gaps = pd.DataFrame(data.get("gaps", []))
    if not gaps.empty:
        st.dataframe(gaps, hide_index=True, use_container_width=True)
        st.markdown("**À sourcer** : demande forte, peu de consultants disponibles. "
                    "**À pousser en prospection** : consultants disponibles sur une compétence "
                    "demandée.")
    tr = pd.DataFrame(data.get("trends", []))
    if not tr.empty:
        st.subheader("Tendances (4 dernières semaines vs 4 précédentes)")
        st.bar_chart(tr.set_index("skill")[["posts_recent", "posts_previous"]])

# ================================================================ Prospection
elif page == "Prospection":
    st.header("Campagne de prospection")
    if st.button("✍️ Générer les brouillons de la semaine"):
        res = api("POST", "/prospection/generate")
        if res:
            st.success(f"{res['created']} brouillon(s) créé(s).")
    status = st.selectbox("Statut", ["draft", "approved", "sent", "rejected"])
    for e in api("GET", "/prospection", params={"status": status}) or []:
        with st.container(border=True):
            st.markdown(f"**{e['prospect_company']}** · {e['contact_role']}")
            st.markdown(f"*Objet : {e['subject']}*")
            st.text(e["body"])
            if status == "draft":
                a, b = st.columns(2)
                if a.button("Approuver (envoi par n8n)", key=f"a{e['email_id']}"):
                    api("POST", f"/prospection/{e['email_id']}/approve",
                        params={"actor": reviewer})
                    st.rerun()
                if b.button("Rejeter", key=f"x{e['email_id']}"):
                    api("POST", f"/prospection/{e['email_id']}/reject",
                        params={"actor": reviewer})
                    st.rerun()

# ======================================================================= RGPD
else:
    st.header("Journal d'audit & RGPD")
    st.markdown("Chaque ingestion, décision et effacement est tracé (qui, quand, quoi). "
                "Les données identifiantes ne sont jamais envoyées au LLM.")
    with st.form("erase"):
        cid = st.text_input("Droit à l'effacement : identifiant du consultant (CAND-…)")
        if st.form_submit_button("Effacer toutes ses données") and cid:
            if api("DELETE", f"/candidates/{cid}", params={"actor": reviewer}):
                st.success(f"{cid} effacé (profil, vecteur, propositions, coffre, document).")
    logs = pd.DataFrame(api("GET", "/audit") or [])
    if not logs.empty:
        st.dataframe(logs, hide_index=True, use_container_width=True)
