"""Génération automatique du dossier de compétences (Word) pour un profil validé.

Le dossier est généré à partir du profil structuré (jamais du CV brut) et adapté au besoin :
les compétences demandées par le client sont mises en avant, l'avis d'adéquation est joint.
Par défaut l'identité est réduite aux initiales (`TF_ANONYMIZE_DOSSIERS=true`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from talentflow.schemas import CandidateProfile, JobNeed, JudgeVerdict

PRIMARY = RGBColor(0x1F, 0x3A, 0x5F)
ACCENT = RGBColor(0x2E, 0x86, 0xAB)
MUTED = RGBColor(0x6B, 0x72, 0x80)


def _shade(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _heading(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = PRIMARY
    # filet sous le titre
    p_pr = p._p.get_or_add_pPr()
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "2E86AB")
    border.append(bottom)
    p_pr.append(border)


def _level(years: float | None) -> str:
    if not years:
        return "●●○○○"
    n = 5 if years >= 8 else 4 if years >= 5 else 3 if years >= 2 else 2
    return "●" * n + "○" * (5 - n)


def safe_filename(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_text).strip("_")[:60]


def build_dossier(
    profile: CandidateProfile,
    need: JobNeed | None,
    display_name: str,
    out_path: str | Path,
    verdict: JudgeVerdict | None = None,
    brand: str = "TalentFlow",
    validated_by: str | None = None,
    show_rate: bool = False,
) -> Path:
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    # En-tête de page
    hp = sec.header.paragraphs[0]
    hr = hp.add_run(f"{brand} — Dossier de compétences")
    hr.font.size = Pt(8)
    hr.font.color.rgb = MUTED
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # Bandeau titre
    band = doc.add_table(rows=1, cols=1)
    band.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = band.rows[0].cells[0]
    _shade(cell, "1F3A5F")
    p = cell.paragraphs[0]
    r = p.add_run(profile.title)
    r.bold = True
    r.font.size = Pt(18)
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    p2 = cell.add_paragraph()
    r2 = p2.add_run(
        f"{display_name}  •  {profile.seniority.label.capitalize()}  •  "
        f"{profile.years_experience:g} ans d'expérience"
    )
    r2.font.size = Pt(11)
    r2.font.color.rgb = RGBColor(0xDB, 0xEA, 0xF5)

    # Informations clés
    doc.add_paragraph()
    facts = [
        ("Disponibilité", profile.availability_date.strftime("%d/%m/%Y")
         if profile.availability_date else "À confirmer"),
        ("Localisation", (profile.location or "—")
         + (f" (mobilité : {', '.join(m.title() for m in profile.mobility)})" if profile.mobility else "")),
        ("Langues", ", ".join(lg.capitalize() for lg in profile.languages) or "—"),
    ]
    if show_rate and profile.daily_rate:
        facts.append(("TJM", f"{profile.daily_rate:.0f} € HT"))
    table = doc.add_table(rows=len(facts), cols=2)
    table.style = "Light Grid Accent 1"
    for i, (k, v) in enumerate(facts):
        table.cell(i, 0).text = k
        table.cell(i, 1).text = v
        table.cell(i, 0).paragraphs[0].runs[0].bold = True
        table.cell(i, 0).width = Cm(4)

    # Adéquation au besoin
    if need:
        _heading(doc, f"Adéquation au besoin : {need.title}")
        if verdict:
            box = doc.add_table(rows=1, cols=1)
            c = box.rows[0].cells[0]
            _shade(c, "EAF4F8")
            c.paragraphs[0].add_run(verdict.justification)
            if verdict.strengths:
                pp = c.add_paragraph()
                pp.add_run("Points forts : ").bold = True
                pp.add_run(" ; ".join(verdict.strengths))
            if verdict.gaps:
                pp = c.add_paragraph()
                pp.add_run("Points d'attention : ").bold = True
                pp.add_run(" ; ".join(verdict.gaps))
        req = [s for s in need.required_skills if s in profile.skill_names]
        if req:
            p = doc.add_paragraph()
            p.add_run("Compétences demandées maîtrisées : ").bold = True
            p.add_run(", ".join(req))

    # Synthèse
    if profile.summary:
        _heading(doc, "Synthèse")
        doc.add_paragraph(profile.summary)

    # Compétences : celles du besoin d'abord, puis par années décroissantes
    _heading(doc, "Compétences techniques")
    priority = set(need.required_skills + need.nice_to_have) if need else set()
    skills = sorted(profile.skills, key=lambda s: (s.name not in priority, -(s.years or 0)))
    st = doc.add_table(rows=1, cols=3)
    st.style = "Light List Accent 1"
    hdr = st.rows[0].cells
    for cell_, label in zip(hdr, ("Compétence", "Pratique", "Niveau")):
        cell_.text = label
    for s in skills[:18]:
        row = st.add_row().cells
        row[0].text = s.name + ("  ★" if s.name in priority else "")
        row[1].text = f"{s.years:g} an(s)" if s.years else "—"
        row[2].text = _level(s.years)
        row[2].paragraphs[0].runs[0].font.color.rgb = ACCENT

    # Expériences
    if profile.experiences:
        _heading(doc, "Expériences professionnelles")
        for e in sorted(profile.experiences, key=lambda e: e.start or "", reverse=True):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(0)
            rt = p.add_run(e.title)
            rt.bold = True
            rt.font.color.rgb = PRIMARY
            if e.company:
                p.add_run(f" — {e.company}")
            period = f"{e.start or '?'} → {'aujourd’hui' if e.end == 'present' else e.end or '?'}"
            meta = doc.add_paragraph()
            meta.paragraph_format.space_after = Pt(2)
            mr = meta.add_run(period + (f"  ({e.duration_months} mois)" if e.duration_months else ""))
            mr.font.size = Pt(9)
            mr.font.color.rgb = MUTED
            if e.summary:
                doc.add_paragraph(e.summary, style="List Bullet")
            if e.skills:
                tp = doc.add_paragraph()
                tr = tp.add_run("Environnement : " + ", ".join(e.skills))
                tr.italic = True
                tr.font.size = Pt(9)

    if profile.education:
        _heading(doc, "Formation")
        for ed in profile.education:
            doc.add_paragraph(ed, style="List Bullet")
    if profile.certifications:
        _heading(doc, "Certifications")
        for ce in profile.certifications:
            doc.add_paragraph(ce, style="List Bullet")

    # Pied de page : traçabilité et confidentialité
    fp = sec.footer.paragraphs[0]
    footer = f"Document confidentiel généré par {brand} le {date.today():%d/%m/%Y}"
    if validated_by:
        footer += f", validé par {validated_by}"
    footer += ". Données traitées conformément au RGPD."
    fr = fp.add_run(footer)
    fr.font.size = Pt(7.5)
    fr.font.color.rgb = MUTED
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out
