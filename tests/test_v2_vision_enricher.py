"""Test V2VisionEnricher — Stage 3 vision enrichment (mock caller)."""
from __future__ import annotations

import json
from pathlib import Path

import fitz

from engine.project_memory import setup_v2_staging_dirs
from engine.v2_map_manager import V2MapManager
from engine.v2_vision_enricher import V2VisionEnricher


def _make_pdf_with_image(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((72, 72), "Diagram context paragraph.", fontsize=12)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), 1)
    pix.set_rect(pix.irect, (0, 128, 255, 255))
    page.insert_image(fitz.Rect(72, 100, 112, 140), pixmap=pix)
    doc.save(str(path))
    doc.close()


def _setup_staged_doc(tmp_path, monkeypatch, slug: str = "vis", doc_id: str = "doc-vis"):
    import engine.project_memory as pm

    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs(slug, doc_id)
    pdf_path = layout.original / "source.pdf"
    _make_pdf_with_image(pdf_path)

    from engine.v2_physical_extractor import PhysicalExtractor
    from engine.v2_chunking_manager import V2ChunkingManager

    PhysicalExtractor(layout).extract("source.pdf")
    V2ChunkingManager(layout).chunk_raw_extract()
    return layout


def test_vision_enricher_writes_insight_and_updates_map(tmp_path, monkeypatch) -> None:
    layout = _setup_staged_doc(tmp_path, monkeypatch)
    mgr = V2MapManager(layout.map_json)
    images = mgr.list_images()
    assert len(images) >= 1
    img_id = images[0]["id"]

    calls: list[dict] = []

    def fake_vision(**kwargs):
        calls.append(kwargs)
        return "Architettura a tre livelli con API gateway."

    enricher = V2VisionEnricher(
        layout,
        map_manager=mgr,
        vision_caller=fake_vision,
        log_fn=lambda _m: None,
    )
    results = enricher.enrich_all_pending()

    assert len(results) == 1
    assert results[0].image_id == img_id
    assert "gateway" in results[0].insight
    assert calls[0]["context_text"]
    assert calls[0]["image_b64"]

    updated = mgr.get_image(img_id)
    assert updated is not None
    assert updated["vision_processed"] is True

    linked = updated.get("linked_chunks") or []
    assert linked
    enriched_file = layout.enriched / f"{linked[0]}.enriched.md"
    assert enriched_file.is_file()
    assert "Vision insight" in enriched_file.read_text(encoding="utf-8")
    assert mgr.get_metadata()["vision_enriched"] is True


def test_vision_enricher_skips_processed_images(tmp_path, monkeypatch) -> None:
    layout = _setup_staged_doc(tmp_path, monkeypatch)
    mgr = V2MapManager(layout.map_json)
    img_id = mgr.list_images()[0]["id"]
    mgr.update_image(img_id, {"vision_processed": True})

    called = {"n": 0}

    def fake_vision(**_kwargs):
        called["n"] += 1
        return "x"

    V2VisionEnricher(layout, map_manager=mgr, vision_caller=fake_vision).enrich_all_pending()
    assert called["n"] == 0


def test_vision_enricher_failure_does_not_crash(tmp_path, monkeypatch) -> None:
    layout = _setup_staged_doc(tmp_path, monkeypatch)
    mgr = V2MapManager(layout.map_json)
    warnings: list[str] = []

    def boom(**_kwargs):
        raise RuntimeError("vision model offline")

    results = V2VisionEnricher(
        layout,
        map_manager=mgr,
        vision_caller=boom,
        log_fn=warnings.append,
    ).enrich_all_pending()

    assert results == []
    assert any("Fallito" in w for w in warnings)
    assert mgr.get_image(mgr.list_images()[0]["id"])["vision_processed"] is False


def test_build_context_includes_neighbor_chunks(tmp_path, monkeypatch) -> None:
    layout = _setup_staged_doc(tmp_path, monkeypatch)
    mgr = V2MapManager(layout.map_json)
    chunks = mgr.list_chunks()
    assert len(chunks) >= 1

    enricher = V2VisionEnricher(
        layout,
        map_manager=mgr,
        vision_caller=lambda **_k: "",
    )
    ctx = enricher._build_context([chunks[0]["id"]])
    assert "Diagram context" in ctx
