"""
V2 Stage 2 — Physical PDF extraction (PyMuPDF + OCR fallback opzionale).

Estrae testo + immagini da ``02_STAGING/<doc_id>/original/``,
inietta placeholder ``[IMG_REF:img_NNN]`` e aggiorna map.json.
OCR Tesseract su pagine raster/scansionate quando il testo nativo è scarso.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

from core.file_io import atomic_write_json
from engine.project_memory import V2StagingLayout, setup_v2_staging_dirs
from engine.v2_map_manager import V2MapManager

logger = logging.getLogger(__name__)

RAW_EXTRACTED_NAME = "raw_extracted.md"
PDF_METADATA_NAME = "pdf_metadata.json"
EXTRACTION_REPORT_NAME = "extraction_report.json"
IMAGE_MANIFEST_NAME = "image_manifest.json"

OCR_MIN_NATIVE_CHARS = 50
OCR_PAGE_MIN_RENDER_BYTES = 4096
OCR_RENDER_DPI = 300

_TESSERACT_WARN_MSG = (
    "Attenzione: OCR richiesto ma Tesseract non è installato o configurato. "
    "Pagina saltata."
)

_TESSERACT_DEFAULT_PATHS: tuple[str, ...] = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
)

_TESSERACT_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    id: str
    path: str
    page: int
    hash: str
    xref: int


@dataclass(slots=True)
class PhysicalExtractionResult:
    pdf_path: Path
    raw_text_path: Path
    page_count: int
    images: list[ExtractedImage] = field(default_factory=list)
    char_count: int = 0
    ocr_used: bool = False


def _resolve_tesseract_cmd() -> str | None:
    """Risolve il path dell'eseguibile Tesseract (senza lock — uso interno)."""
    env_path = os.environ.get("TESSERACT_CMD_PATH", "").strip()
    candidates: list[str] = []
    if env_path:
        candidates.append(env_path)
    candidates.extend(_TESSERACT_DEFAULT_PATHS)
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)

    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def configure_tesseract_cmd() -> str | None:
    """
    Configura ``pytesseract.pytesseract.tesseract_cmd``.

    Ordine: ``TESSERACT_CMD_PATH`` → percorsi standard → ``PATH``.
    Restituisce il path risolto o ``None`` se non trovato.
    Thread-safe: lock condiviso con ``_run_tesseract_ocr``.
    """
    try:
        import pytesseract
    except ImportError:
        return None

    with _TESSERACT_LOCK:
        resolved = _resolve_tesseract_cmd()
        if resolved:
            pytesseract.pytesseract.tesseract_cmd = resolved
        return resolved


def _page_content_byte_size(page: fitz.Page) -> int:
    try:
        return len(page.read_contents() or b"")
    except Exception:
        return 0


def _needs_ocr_fallback(page: fitz.Page, native_char_count: int) -> bool:
    if native_char_count >= OCR_MIN_NATIVE_CHARS:
        return False
    if page.get_images(full=True):
        return True
    return _page_content_byte_size(page) >= OCR_PAGE_MIN_RENDER_BYTES


def _run_tesseract_ocr(page: fitz.Page, *, dpi: int = OCR_RENDER_DPI) -> str:
    """
    OCR pagina via Tesseract.

    Il ``Pixmap`` MuPDF viene rilasciato in ``finally`` (``pix = None``) anche se
    ``Image.frombytes`` o ``image_to_string`` sollevano — ``__del__`` fitz non
    è affidabile su Windows/CPython. I campioni vengono copiati in ``bytes``
    prima del rilascio così Tesseract non trattiene VRAM durante l'inferenza.
    Configurazione + ``image_to_string`` sotto ``_TESSERACT_LOCK`` (no race multi-worker).
    """
    try:
        import pytesseract
    except ImportError:
        raise FileNotFoundError("tesseract executable not found") from None

    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    try:
        samples = bytes(pix.samples)
        width, height = pix.width, pix.height
    finally:
        pix = None  # type: ignore[assignment]

    img = Image.frombytes("RGB", (width, height), samples)
    with _TESSERACT_LOCK:
        resolved = _resolve_tesseract_cmd()
        if resolved is None:
            raise FileNotFoundError("tesseract executable not found")
        pytesseract.pytesseract.tesseract_cmd = resolved
        return (pytesseract.image_to_string(img) or "").strip()


class PhysicalExtractor:
    """
    Parser estrazione fisica PDF — ``fitz`` (PyMuPDF) + OCR Tesseract opzionale.

    Input: PDF in ``layout.original/`` (``02_STAGING/<doc_id>/original/``).
    Output: ``extracted/text/raw_extracted.md``, immagini in ``extracted/images/``,
    OCR in ``extracted/ocr/``, metadati in ``extracted/metadata/``.
    """

    def __init__(
        self,
        layout: V2StagingLayout,
        *,
        map_manager: V2MapManager | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.layout = layout
        self.map_manager = map_manager or V2MapManager(layout.map_json)
        self._log_fn = log_fn or (lambda msg: logger.warning(msg))

    @classmethod
    def for_project(
        cls,
        slug: str,
        document_id: str,
        *,
        ensure_dirs: bool = True,
        log_fn: Callable[[str], None] | None = None,
    ) -> PhysicalExtractor:
        layout = (
            setup_v2_staging_dirs(slug, document_id)
            if ensure_dirs
            else _layout_from_existing(slug, document_id)
        )
        return cls(layout, log_fn=log_fn)

    def extract(self, pdf_name: str | None = None) -> PhysicalExtractionResult:
        pdf_path = self._resolve_pdf(pdf_name)
        self.map_manager.update_document(source_file=pdf_path.name)

        images_out: list[ExtractedImage] = []
        page_texts: list[str] = []
        image_seq = 0
        ocr_used = False

        with fitz.open(pdf_path) as doc:
            # ``doc`` resta aperto per tutta l'estrazione: il ``with`` garantisce
            # chiusura del file anche se ``_extract_page`` / ``_save_pixmap_png``
            # sollevano eccezioni (evita lock del PDF su Windows fino al GC).
            pdf_meta = dict(doc.metadata or {})
            atomic_write_json(
                self.layout.extracted_metadata / PDF_METADATA_NAME,
                {"source": pdf_path.name, "metadata": pdf_meta},
            )

            for page_index in range(len(doc)):
                page = doc[page_index]
                page_no = page_index + 1
                page_body, page_images, image_seq, page_ocr = self._extract_page(
                    doc,
                    page,
                    page_no,
                    start_index=image_seq,
                )
                images_out.extend(page_images)
                if page_ocr:
                    ocr_used = True
                if page_body.strip():
                    page_texts.append(f"## Page {page_no}\n\n{page_body.strip()}")

        raw_md = self._assemble_document(pdf_path, page_texts, ocr_used=ocr_used)
        raw_path = self.layout.extracted_text / RAW_EXTRACTED_NAME
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw_md, encoding="utf-8", newline="\n")

        self._write_sidecar_metadata(
            images_out,
            pdf_path,
            len(page_texts),
            ocr_used=ocr_used,
        )
        self._update_map(
            pdf_path,
            images_out,
            page_count=len(page_texts),
            char_count=len(raw_md),
            ocr_used=ocr_used,
        )

        logger.info(
            "Physical extraction completata: %s pages=%d images=%d ocr=%s",
            pdf_path.name,
            len(page_texts),
            len(images_out),
            ocr_used,
        )
        return PhysicalExtractionResult(
            pdf_path=pdf_path,
            raw_text_path=raw_path,
            page_count=len(page_texts),
            images=images_out,
            char_count=len(raw_md),
            ocr_used=ocr_used,
        )

    def _resolve_pdf(self, pdf_name: str | None) -> Path:
        original = self.layout.original
        if not original.is_dir():
            raise FileNotFoundError(f"Cartella original mancante: {original}")

        if pdf_name:
            candidate = original / pdf_name
            if not candidate.is_file():
                raise FileNotFoundError(f"PDF non trovato: {candidate}")
            return candidate

        pdfs = sorted(original.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"Nessun PDF in {original}")
        if len(pdfs) > 1:
            logger.warning(
                "Più PDF in original/ — uso il primo: %s (altri: %s)",
                pdfs[0].name,
                [p.name for p in pdfs[1:]],
            )
        return pdfs[0]

    def _extract_page(
        self,
        doc: fitz.Document,
        page: fitz.Page,
        page_no: int,
        *,
        start_index: int,
    ) -> tuple[str, list[ExtractedImage], int, bool]:
        """
        Estrae testo, immagini e OCR opzionale da una singola pagina.

        ``doc`` e ``page`` sono riferimenti al documento aperto da ``extract()``;
        devono restare entro il ``with fitz.open(...)`` del chiamante.
        ``fitz.Rect`` in ``_xref_for_bbox`` è un valore leggero — nessun cleanup.
        """
        blocks = page.get_text("blocks") or []
        blocks = sorted(blocks, key=lambda b: (round(b[1], 2), round(b[0], 2)))

        saved_xrefs: dict[int, str] = {}
        page_images: list[ExtractedImage] = []
        parts: list[str] = []
        seq = start_index

        for block in blocks:
            if len(block) < 7:
                continue
            x0, y0, x1, y1, text, _block_no, block_type = block[:7]
            if block_type == 0:
                chunk = (text or "").strip()
                if chunk:
                    parts.append(chunk)
                continue

            if block_type != 1:
                continue

            bbox = fitz.Rect(x0, y0, x1, y1)
            xref = self._xref_for_bbox(page, bbox)
            if xref is None:
                seq += 1
                img_id = _format_image_id(seq)
                parts.append(f"[IMG_REF:{img_id}]")
                continue

            if xref not in saved_xrefs:
                seq += 1
                img_id = _format_image_id(seq)
                rel_path = f"extracted/images/{img_id}.png"
                out_path = self.layout.extracted_images / f"{img_id}.png"
                file_hash = _save_pixmap_png(doc, xref, out_path)
                saved_xrefs[xref] = img_id
                page_images.append(
                    ExtractedImage(
                        id=img_id,
                        path=rel_path,
                        page=page_no,
                        hash=file_hash,
                        xref=xref,
                    )
                )
            else:
                img_id = saved_xrefs[xref]

            parts.append(f"[IMG_REF:{img_id}]")

        for img in page.get_images(full=True):
            xref = int(img[0])
            if xref in saved_xrefs:
                continue
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            seq += 1
            img_id = _format_image_id(seq)
            rel_path = f"extracted/images/{img_id}.png"
            out_path = self.layout.extracted_images / f"{img_id}.png"
            file_hash = _save_pixmap_png(doc, xref, out_path)
            saved_xrefs[xref] = img_id
            page_images.append(
                ExtractedImage(
                    id=img_id,
                    path=rel_path,
                    page=page_no,
                    hash=file_hash,
                    xref=xref,
                )
            )
            parts.append(f"[IMG_REF:{img_id}]")

        page_body = "\n\n".join(parts)
        native_len = len(page_body.strip()) or len((page.get_text("text") or "").strip())
        page_ocr_used = False

        if _needs_ocr_fallback(page, native_len):
            ocr_text = self._try_ocr_page(page, page_no)
            if ocr_text:
                page_ocr_used = True
                if page_body.strip():
                    page_body = f"{page_body.strip()}\n\n{ocr_text}"
                else:
                    page_body = ocr_text

        return page_body, page_images, seq, page_ocr_used

    def _try_ocr_page(self, page: fitz.Page, page_no: int) -> str:
        """OCR fallback; errori Tesseract/Pixmap non propagano al chiamante."""
        try:
            ocr_text = _run_tesseract_ocr(page)
        except Exception as exc:
            logger.debug("OCR fallito pagina %d: %s", page_no, exc)
            self._log_fn(f"{_TESSERACT_WARN_MSG} (pagina {page_no})")
            return ""

        if not ocr_text:
            return ""

        ocr_dir = self.layout.extracted_ocr
        ocr_dir.mkdir(parents=True, exist_ok=True)
        ocr_path = ocr_dir / f"page_{page_no:03d}_ocr.txt"
        ocr_path.write_text(ocr_text + "\n", encoding="utf-8", newline="\n")
        return ocr_text

    @staticmethod
    def _xref_for_bbox(page: fitz.Page, bbox: fitz.Rect) -> int | None:
        """Trova xref immagine con massima sovrapposizione al bbox (Rect = valore, no leak)."""
        best: tuple[float, int] | None = None
        for img in page.get_images(full=True):
            xref = int(img[0])
            for rect in page.get_image_rects(xref):
                inter = rect & bbox
                if inter.is_empty:
                    continue
                area = float(inter.get_area())
                if best is None or area > best[0]:
                    best = (area, xref)
        return best[1] if best else None

    @staticmethod
    def _assemble_document(
        pdf_path: Path,
        page_texts: list[str],
        *,
        ocr_used: bool = False,
    ) -> str:
        ocr_note = " + Tesseract OCR fallback" if ocr_used else ""
        header = (
            f"# Raw extraction — {pdf_path.name}\n\n"
            f"> V2 physical extract (PyMuPDF{ocr_note}). "
            f"Placeholder immagini: `[IMG_REF:img_NNN]`\n\n"
        )
        if not page_texts:
            return (
                header
                + "_(nessun testo estratto — possibile PDF raster; "
                "installare Tesseract per OCR)_\n"
            )
        return header + "\n\n".join(page_texts) + "\n"

    def _write_sidecar_metadata(
        self,
        images: list[ExtractedImage],
        pdf_path: Path,
        page_count: int,
        *,
        ocr_used: bool,
    ) -> None:
        meta_dir = self.layout.extracted_metadata
        meta_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            meta_dir / IMAGE_MANIFEST_NAME,
            {
                "source": pdf_path.name,
                "images": [
                    {
                        "id": img.id,
                        "path": img.path,
                        "page": img.page,
                        "hash": img.hash,
                        "xref": img.xref,
                    }
                    for img in images
                ],
            },
        )
        atomic_write_json(
            meta_dir / EXTRACTION_REPORT_NAME,
            {
                "source": pdf_path.name,
                "page_count": page_count,
                "image_count": len(images),
                "raw_text": f"extracted/text/{RAW_EXTRACTED_NAME}",
                "engine": "fitz+tesseract" if ocr_used else "fitz",
                "ocr_used": ocr_used,
            },
        )

    def _update_map(
        self,
        pdf_path: Path,
        images: list[ExtractedImage],
        *,
        page_count: int,
        char_count: int,
        ocr_used: bool,
    ) -> None:
        title = pdf_path.stem
        meta_path = self.layout.extracted_metadata / PDF_METADATA_NAME
        if meta_path.is_file():
            import json

            try:
                wrapped = json.loads(meta_path.read_text(encoding="utf-8"))
                title = (wrapped.get("metadata") or {}).get("title") or title
            except (json.JSONDecodeError, OSError):
                pass

        self.map_manager.update_metadata(
            {
                "title": title,
                "page_count": page_count,
                "ocr_used": ocr_used,
            }
        )
        self.map_manager.set_stage(
            "physical_extraction",
            "completed",
            char_count=char_count,
            image_count=len(images),
            ocr_used=ocr_used,
        )

        for img in images:
            try:
                self.map_manager.add_image(
                    {
                        "id": img.id,
                        "path": img.path,
                        "page": img.page,
                        "hash": img.hash,
                        "vision_processed": False,
                        "linked_chunks": [],
                    }
                )
            except KeyError:
                self.map_manager.update_image(
                    img.id,
                    {
                        "path": img.path,
                        "page": img.page,
                        "hash": img.hash,
                    },
                )


def _format_image_id(seq: int) -> str:
    return f"img_{seq:03d}"


def _save_pixmap_png(doc: fitz.Document, xref: int, out_path: Path) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix = fitz.Pixmap(doc, xref)
    try:
        if pix.n - pix.alpha > 3:
            rgb = fitz.Pixmap(fitz.csRGB, pix)
            try:
                rgb.save(str(out_path))
            finally:
                rgb = None  # type: ignore[assignment]
        else:
            pix.save(str(out_path))
    finally:
        pix = None  # type: ignore[assignment]

    data = out_path.read_bytes()
    return hashlib.md5(data).hexdigest()


def _layout_from_existing(slug: str, document_id: str) -> V2StagingLayout:
    from engine.project_memory import v2_document_staging_dir

    root = v2_document_staging_dir(slug, document_id)
    return V2StagingLayout(
        root=root,
        original=root / "original",
        extracted_text=root / "extracted" / "text",
        extracted_images=root / "extracted" / "images",
        extracted_tables=root / "extracted" / "tables",
        extracted_ocr=root / "extracted" / "ocr",
        extracted_metadata=root / "extracted" / "metadata",
        chunks=root / "chunks",
        enriched=root / "enriched",
        rolling_memory=root / "rolling_memory",
        conflicts=root / "conflicts",
        conflicts_unresolved=root / "conflicts" / "unresolved",
        conflicts_resolved=root / "conflicts" / "resolved",
        audit=root / "audit",
        map_json=root / "map.json",
        conflict_log_json=root / "conflicts" / "conflict_log.json",
    )
