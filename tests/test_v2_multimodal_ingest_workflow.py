"""Test V2MultimodalIngestWorkflow — end-to-end orchestration (no LLM)."""
from __future__ import annotations

from pathlib import Path

import fitz

from engine.orchestrator import OrchestratorState
from engine.project_memory import ingest_dir
from engine.v2_map_manager import V2MapManager
from workflows.v2_multimodal_ingest import V2MultimodalIngestWorkflow


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "V2 workflow test paragraph one.", fontsize=11)
    page.insert_text((72, 100), "Second paragraph for chunking.", fontsize=11)
    doc.save(str(path))
    doc.close()


def test_v2_multimodal_ingest_workflow_e2e(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-wf"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    proj = tmp_path / "projects" / slug
    ingest = proj / "01_INGEST"
    ingest.mkdir(parents=True)
    (proj / "04_MEMORY").mkdir(parents=True)
    (proj / "project.json").write_text("{}", encoding="utf-8")

    pdf = ingest / "sample-doc.pdf"
    _make_pdf(pdf)

    state = OrchestratorState()
    logs: list[str] = []
    ctx = {
        "slug": slug,
        "stop_event": state.stop_event,
        "orchestrator": state,
        "log_fn": logs.append,
        "file_index": 0,
        "files_in_job": 1,
    }

    result = V2MultimodalIngestWorkflow().run(pdf, ctx)

    assert result["status"] == "ok"
    assert result["chunks"] >= 1
    assert result["document_id"] == "sample-doc"

    staging = proj / "02_STAGING" / "sample-doc"
    assert (staging / "extracted" / "text" / "raw_extracted.md").is_file()
    assert (staging / "chunks" / "chunk_001.md").is_file()
    assert (staging / "map.json").is_file()

    mgr = V2MapManager(staging / "map.json")
    data = mgr.to_dict()
    assert data["stages"]["physical_extraction"]["status"] == "completed"
    assert data["stages"]["physical_chunking"]["status"] == "completed"
    assert len(data["chunks"]) >= 1
    assert any("[V2_INGEST]" in m for m in logs)


def test_v2_ingest_beta_registered() -> None:
    from engine.workflow_runner import WorkflowRunner

    wf = WorkflowRunner().get_workflow("v2_ingest_beta")
    assert wf is not None
    assert wf.capabilities.requires_llm is False

    meta = {m["id"]: m for m in WorkflowRunner.registered_with_meta()}
    assert "v2_ingest_beta" in meta
    assert "Multimodal" in meta["v2_ingest_beta"]["label"]


def test_run_project_empty_ingest_returns_skipped(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-empty"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    ingest = tmp_path / "projects" / slug / "01_INGEST"
    ingest.mkdir(parents=True)

    logs: list[str] = []
    out = V2MultimodalIngestWorkflow().run_project(
        {"slug": slug, "log_fn": logs.append},
    )

    assert out["status"] == "skipped"
    assert out["reason"] == "no_files"
    assert any("Nessun file trovato" in m for m in logs)


def test_run_project_processes_md_without_index_error(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-md"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    proj = tmp_path / "projects" / slug
    ingest = proj / "01_INGEST"
    ingest.mkdir(parents=True)
    (proj / "04_MEMORY").mkdir(parents=True)
    (ingest / "notes.md").write_text("# Notes\n\nHello markdown.", encoding="utf-8")

    out = V2MultimodalIngestWorkflow().run_project({"slug": slug, "log_fn": lambda _m: None})
    assert out["processed"] == 1
    assert (proj / "02_STAGING" / "notes" / "chunks" / "chunk_001.md").is_file()


def test_workflow_runs_vision_stage_when_caller_injected(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-vis-wf"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    proj = tmp_path / "projects" / slug
    ingest = proj / "01_INGEST"
    ingest.mkdir(parents=True)
    (proj / "04_MEMORY").mkdir(parents=True)

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Vision workflow test.", fontsize=11)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 30), 1)
    pix.set_rect(pix.irect, (255, 0, 0, 255))
    page.insert_image(fitz.Rect(72, 100, 102, 130), pixmap=pix)
    pdf = ingest / "vis-doc.pdf"
    doc.save(str(pdf))
    doc.close()

    logs: list[str] = []
    result = V2MultimodalIngestWorkflow().run(
        pdf,
        {
            "slug": slug,
            "log_fn": logs.append,
            "vision_caller": lambda **_k: "Mock vision insight.",
        },
    )

    assert result["status"] == "ok"
    assert result["vision_enriched"] >= 1
    staging = proj / "02_STAGING" / "vis-doc"
    mgr = V2MapManager(staging / "map.json")
    assert mgr.get_metadata()["vision_enriched"] is True
    assert any("Vision:" in m for m in logs)


def test_workflow_runs_rolling_stage_when_extractor_injected(tmp_path, monkeypatch) -> None:
    import json
    import engine.project_memory as pm

    slug = "v2-roll-wf"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    proj = tmp_path / "projects" / slug
    ingest = proj / "01_INGEST"
    ingest.mkdir(parents=True)
    (proj / "04_MEMORY").mkdir(parents=True)
    (ingest / "roll-doc.md").write_text(
        "# Doc\n\nRolling memory workflow test paragraph.",
        encoding="utf-8",
    )

    def fake_extractor(*, text, context, max_tokens):
        return json.dumps(
            {
                "facts": [
                    {
                        "claim": "Test fact",
                        "section": "Doc",
                        "confidence": "high",
                    }
                ],
                "entities": ["TestEntity"],
                "decisions": [],
            }
        )

    logs: list[str] = []
    result = V2MultimodalIngestWorkflow().run(
        ingest / "roll-doc.md",
        {
            "slug": slug,
            "log_fn": logs.append,
            "fact_extractor": fake_extractor,
        },
    )

    assert result["status"] == "ok"
    assert result["rolling_processed"] >= 1
    staging = proj / "02_STAGING" / "roll-doc"
    mgr = V2MapManager(staging / "map.json")
    assert mgr.get_rolling_memory()["last_merged_chunk_id"] is not None
    assert (staging / "rolling_memory" / "rolling_state.json").is_file()
    assert any("Rolling:" in m for m in logs)


def test_env_flags_resolve_llm_callers(monkeypatch) -> None:
    from workflows.v2_multimodal_ingest import (
        _env_enabled,
        _resolve_fact_extractor,
        _resolve_vision_caller,
    )

    monkeypatch.delenv("V2_VISION_ENABLED", raising=False)
    monkeypatch.delenv("V2_ROLLING_CONTEXT_ENABLED", raising=False)
    assert _resolve_vision_caller({}) is None
    assert _resolve_fact_extractor({}) is None

    monkeypatch.setenv("V2_VISION_ENABLED", "true")
    assert _resolve_vision_caller({}) is not None
    assert _env_enabled("V2_VISION_ENABLED")

    monkeypatch.setenv("V2_ROLLING_CONTEXT_ENABLED", "1")
    assert _resolve_fact_extractor({}) is not None

    custom = lambda **_k: "x"
    assert _resolve_vision_caller({"vision_caller": custom}) is custom


def test_workflow_rolling_env_without_vision(tmp_path, monkeypatch) -> None:
    import json
    import engine.project_memory as pm

    monkeypatch.setenv("V2_ROLLING_CONTEXT_ENABLED", "true")
    monkeypatch.delenv("V2_VISION_ENABLED", raising=False)

    slug = "v2-roll-env"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    ingest = tmp_path / "projects" / slug / "01_INGEST"
    ingest.mkdir(parents=True)
    (ingest / "doc.md").write_text("# T\n\nFact paragraph here.", encoding="utf-8")

    def fake_extractor(*, text, context, max_tokens):
        return json.dumps(
            {
                "facts": [{"claim": "Has fact", "section": "T", "confidence": "high"}],
                "entities": [],
                "decisions": [],
            }
        )

    monkeypatch.setattr(
        "workflows.v2_multimodal_ingest._resolve_fact_extractor",
        lambda _ctx: fake_extractor,
    )
    monkeypatch.setattr(
        "workflows.v2_multimodal_ingest._resolve_vision_caller",
        lambda _ctx: None,
    )

    result = V2MultimodalIngestWorkflow().run(
        ingest / "doc.md",
        {"slug": slug, "log_fn": lambda _m: None},
    )
    assert result["rolling_processed"] >= 1
    assert result["vision_enriched"] == 0
