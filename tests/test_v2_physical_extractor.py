"""Test PhysicalExtractor — PyMuPDF physical PDF extraction."""
from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from engine.project_memory import setup_v2_staging_dirs
from engine.v2_map_manager import V2MapManager
from engine.v2_physical_extractor import RAW_EXTRACTED_NAME, PhysicalExtractor


def _make_pdf_with_text_and_image(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((72, 72), "Before image paragraph.", fontsize=12)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), 1)
    pix.set_rect(pix.irect, (255, 0, 0, 255))
    page.insert_image(fitz.Rect(72, 100, 112, 140), pixmap=pix)
    page.insert_text((72, 160), "After image paragraph.", fontsize=12)
    doc.save(str(path))
    doc.close()


def test_physical_extractor_text_and_image(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-extract"
    doc_id = "doc-pdf-001"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    layout = setup_v2_staging_dirs(slug, doc_id)
    pdf_path = layout.original / "source.pdf"
    _make_pdf_with_text_and_image(pdf_path)

    extractor = PhysicalExtractor(layout)
    result = extractor.extract("source.pdf")

    assert result.raw_text_path.is_file()
    raw = result.raw_text_path.read_text(encoding="utf-8")
    assert "Before image" in raw
    assert "After image" in raw
    assert "[IMG_REF:img_001]" in raw
    assert RAW_EXTRACTED_NAME in str(result.raw_text_path)

    img_file = layout.extracted_images / "img_001.png"
    assert img_file.is_file()
    assert result.page_count >= 1
    assert len(result.images) >= 1
    assert result.images[0].id == "img_001"

    mgr = V2MapManager(layout.map_json)
    data = mgr.to_dict()
    assert len(data["physical_assets"]["images"]) >= 1
    assert data["physical_assets"]["images"][0]["id"] == "img_001"
    assert data["stages"]["physical_extraction"]["status"] == "completed"


def test_physical_extractor_missing_pdf_raises(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs("empty", "no-pdf")
    extractor = PhysicalExtractor(layout)
    with pytest.raises(FileNotFoundError):
        extractor.extract()


def test_for_project_factory(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs("fac", "d1")
    pdf_path = layout.original / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Only text here.")
    doc.save(str(pdf_path))
    doc.close()

    result = PhysicalExtractor.for_project("fac", "d1").extract()
    assert "Only text here" in result.raw_text_path.read_text(encoding="utf-8")
    report = json.loads(
        (layout.extracted_metadata / "extraction_report.json").read_text(encoding="utf-8")
    )
    assert report["engine"] == "fitz"
    assert report["image_count"] == 0
    assert report.get("ocr_used") is False


def _make_raster_like_pdf(path: Path) -> None:
    """PDF senza testo nativo — solo immagine full-page."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 80), 1)
    pix.set_rect(pix.irect, (240, 240, 240, 255))
    page.insert_image(fitz.Rect(0, 0, 300, 200), pixmap=pix)
    doc.save(str(path))
    doc.close()


def test_ocr_fallback_uses_tesseract_when_native_text_sparse(
    tmp_path, monkeypatch
) -> None:
    import engine.project_memory as pm

    slug = "v2-ocr"
    doc_id = "scan-doc"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    layout = setup_v2_staging_dirs(slug, doc_id)
    pdf_path = layout.original / "scan.pdf"
    _make_raster_like_pdf(pdf_path)

    monkeypatch.setattr(
        "engine.v2_physical_extractor._run_tesseract_ocr",
        lambda page, dpi=300: "Testo OCR da scansione.",
    )

    result = PhysicalExtractor(layout).extract("scan.pdf")

    raw = result.raw_text_path.read_text(encoding="utf-8")
    assert "Testo OCR da scansione" in raw
    assert result.ocr_used is True
    assert (layout.extracted_ocr / "page_001_ocr.txt").is_file()
    mgr = V2MapManager(layout.map_json)
    assert mgr.get_metadata()["ocr_used"] is True


def test_ocr_failure_does_not_crash_extraction(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-ocr-fail"
    doc_id = "scan-fail"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    layout = setup_v2_staging_dirs(slug, doc_id)
    _make_raster_like_pdf(layout.original / "scan.pdf")

    def _boom(_page, dpi=300):
        raise RuntimeError("tesseract missing")

    monkeypatch.setattr("engine.v2_physical_extractor._run_tesseract_ocr", _boom)

    warnings: list[str] = []
    result = PhysicalExtractor(layout, log_fn=warnings.append).extract("scan.pdf")

    assert any("Tesseract non è installato" in w for w in warnings)
    assert result.ocr_used is False
    assert result.raw_text_path.is_file()


def test_configure_tesseract_cmd_env(monkeypatch, tmp_path) -> None:
    from engine.v2_physical_extractor import configure_tesseract_cmd

    fake = tmp_path / "tesseract.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("TESSERACT_CMD_PATH", str(fake))
    assert configure_tesseract_cmd() == str(fake)


def test_run_tesseract_ocr_releases_pixmap_before_tesseract(monkeypatch) -> None:
    """Pixmap rilasciato in finally anche se PIL/Tesseract falliscono dopo."""
    from engine.v2_physical_extractor import _run_tesseract_ocr

    released: list[str] = []

    class FakePixmap:
        width = 2
        height = 2
        samples = b"\xff" * 12

        def __del__(self) -> None:
            released.append("pix")

    class FakePage:
        def get_pixmap(self, dpi: int = 300) -> FakePixmap:
            return FakePixmap()

    monkeypatch.setattr(
        "engine.v2_physical_extractor._resolve_tesseract_cmd",
        lambda: r"C:\fake\tesseract.exe",
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("tesseract boom - pixmap must already be released")

    monkeypatch.setattr("pytesseract.image_to_string", _boom)

    with pytest.raises(RuntimeError, match="tesseract boom"):
        _run_tesseract_ocr(FakePage())  # type: ignore[arg-type]

    assert released == ["pix"]
