"""Conversion des documents reçus (PDF, DOCX, TXT, MD, HTML) en texte.

Docling est utilisé s'il est installé (meilleure gestion des mises en page en colonnes
et des tableaux) ; sinon on retombe sur pypdf / python-docx, sans dépendance lourde.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".html", ".htm"}


@dataclass
class ParsedDocument:
    filename: str
    text: str
    sha256: str
    parser: str


class UnsupportedFormatError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_bytes(data: bytes, filename: str, prefer_docling: bool = True) -> ParsedDocument:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFormatError(f"Format non supporté : {ext} ({filename})")

    text, parser = None, ""
    if prefer_docling and ext in {".pdf", ".docx"}:
        text = _try_docling(data, filename)
        parser = "docling" if text else ""

    if text is None:
        if ext == ".pdf":
            text, parser = _parse_pdf(data), "pypdf"
        elif ext == ".docx":
            text, parser = _parse_docx(data), "python-docx"
        elif ext in {".html", ".htm"}:
            text, parser = _strip_html(data.decode("utf-8", errors="replace")), "html"
        else:
            text, parser = data.decode("utf-8", errors="replace"), "text"

    text = _clean(text)
    if not text.strip():
        raise ValueError(
            f"Aucun texte extrait de {filename} (PDF scanné ? activer l'OCR de Docling)"
        )
    return ParsedDocument(filename=filename, text=text, sha256=sha256_bytes(data), parser=parser)


def parse_file(path: str | Path, prefer_docling: bool = True) -> ParsedDocument:
    p = Path(path)
    return parse_bytes(p.read_bytes(), p.name, prefer_docling=prefer_docling)


# ---------------------------------------------------------------- back-ends
def _try_docling(data: bytes, filename: str) -> str | None:
    try:  # pragma: no cover - dépendance optionnelle
        from docling.datamodel.base_models import DocumentStream
        from docling.document_converter import DocumentConverter
    except Exception:  # noqa: BLE001
        return None
    try:  # pragma: no cover
        stream = DocumentStream(name=filename, stream=io.BytesIO(data))
        result = DocumentConverter().convert(stream)
        return result.document.export_to_markdown()
    except Exception:  # noqa: BLE001
        return None


def _parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines: list[str] = []
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in block.iter() if t.tag.endswith("}t"))
            lines.append(text)
        elif tag == "tbl":
            for row in block.iter():
                if row.tag.endswith("}tr"):
                    cells = []
                    for cell in row.iter():
                        if cell.tag.endswith("}tc"):
                            cells.append(
                                "".join(t.text or "" for t in cell.iter() if t.tag.endswith("}t"))
                            )
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", html, flags=re.I)
    return re.sub(r"<[^>]+>", "", html)


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace(" ", " ").replace("\t", " ")
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
