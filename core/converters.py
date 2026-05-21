"""
Conversione file sorgente → Markdown normalizzato (staging).
Logica allineata a tools/Motore_Dvamocles/ingest.py (estrazione testo).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

_WHITESPACE_RE = re.compile(r"\s+")
_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


@dataclass(slots=True)
class ConvertResult:
    source: Path
    rel_path: str
    staging_path: Path | None
    char_count: int
    ok: bool
    error: str | None = None


def _read_text(path: Path) -> str:
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s).strip()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return _normalize(soup.get_text(separator="\n"))


def json_to_text(path: Path) -> str:
    try:
        obj = json.loads(_read_text(path))
    except Exception as e:
        return f"_Errore parsing JSON: {e}_"
    if isinstance(obj, dict):
        for key in ("content", "body", "text", "markdown"):
            if key in obj and isinstance(obj[key], str):
                return _normalize(obj[key])
        return _normalize(json.dumps(obj, ensure_ascii=False, indent=2)[:50000])
    return _normalize(json.dumps(obj, ensure_ascii=False, indent=2)[:50000])


def pdf_to_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "_pypdf non installato — impossibile leggere PDF_"
    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
        return _normalize("\n\n".join(parts)) or "_PDF senza testo estratto_"
    except Exception as e:
        return f"_Errore PDF: {e}_"


def docx_to_text(path: Path) -> str:
    try:
        from docx import Document
    except ImportError:
        return "_python-docx non installato_"
    try:
        doc = Document(str(path))
        return _normalize("\n".join(p.text for p in doc.paragraphs if p.text))
    except Exception as e:
        return f"_Errore DOCX: {e}_"


def extract_plain(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt", ".csv", ".log", ".xml"}:
        return _normalize(_read_text(path))
    if suffix in {".html", ".htm"}:
        return html_to_text(_read_text(path))
    if suffix == ".json":
        return json_to_text(path)
    if suffix == ".pdf":
        return pdf_to_text(path)
    if suffix == ".docx":
        return docx_to_text(path)
    return ""


def source_to_markdown(source: Path, rel: str) -> str:
    body = extract_plain(source)
    if not body:
        body = "_Nessun testo estratto._"
    header = (
        "---\n"
        f"source_file: \"{rel.replace(chr(92), '/')}\"\n"
        f"source_ext: \"{source.suffix.lower()}\"\n"
        f"pipeline: local_doc_pipeline\n"
        "---\n\n"
    )
    return header + f"# Sorgente: {Path(rel).name}\n\n{body}\n"


def convert_file(source: Path, source_root: Path, staging_root: Path) -> ConvertResult:
    rel = source.relative_to(source_root).as_posix()
    try:
        md = source_to_markdown(source, rel)
        h = hashlib.sha256(rel.encode()).hexdigest()[:16]
        safe = re.sub(r"[^\w\-.]+", "_", Path(rel).stem)[:60]
        dest = staging_root / f"{safe}__{h}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8", newline="\n")
        return ConvertResult(
            source=source,
            rel_path=rel,
            staging_path=dest,
            char_count=len(md),
            ok=True,
        )
    except Exception as e:
        return ConvertResult(
            source=source,
            rel_path=rel,
            staging_path=None,
            char_count=0,
            ok=False,
            error=str(e),
        )
