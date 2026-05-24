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
