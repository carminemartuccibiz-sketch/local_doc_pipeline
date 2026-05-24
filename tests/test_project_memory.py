"""Test helper output workflow + integrazione OrchestratorState."""
from __future__ import annotations

import json
from pathlib import Path

from engine.orchestrator import OrchestratorState
from engine.project_memory import (
    save_workflow_output,
    save_workflow_output_markdown,
    workflow_output_path,
    workflow_outputs_index_path,
)


def test_save_workflow_output_writes_03_output(tmp_path: Path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "demo-proj"
    base = tmp_path / "projects" / slug
    for sub in ("01_INGEST", "02_REFERENCE", "03_OUTPUT", "04_MEMORY"):
        (base / sub).mkdir(parents=True)
    (base / "project.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    state = OrchestratorState()
    state.init_current_job(
        project=slug,
        workflow="blog_post",
        status="running",
        files_total=2,
        files_completed=0,
    )

    out = save_workflow_output(
        slug,
        "blog_post",
        "article.md",
        "Corpo del post.",
        as_markdown=True,
        source_file="note.txt",
        state=state,
        bump_progress=True,
        current_file="note.txt",
    )

    assert out.is_file()
    assert "03_OUTPUT" in str(out)
    assert "blog_post" in str(out)
    text = out.read_text(encoding="utf-8")
    assert "Corpo del post" in text
    assert "# Blog Post" in text or "Blog Post" in text

    snap = state.get_current_job_snapshot()
    assert snap is not None
    assert snap["files_completed"] == 1
    assert snap["last_output"] == "blog_post/article.md"

    index = json.loads(workflow_outputs_index_path(slug).read_text(encoding="utf-8"))
    assert len(index["outputs"]) == 1
    assert index["outputs"][0]["workflow"] == "blog_post"


def test_workflow_output_path_sanitizes_filename(tmp_path: Path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "x"
    (tmp_path / "projects" / slug / "03_OUTPUT").mkdir(parents=True)
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    p = workflow_output_path(slug, "code_analysis", "../../etc/passwd")
    assert ".." not in p.name
    assert p.parent.name == "code_analysis"


def test_save_workflow_output_markdown_shortcut(tmp_path: Path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "m"
    (tmp_path / "projects" / slug / "03_OUTPUT").mkdir(parents=True)
    (tmp_path / "projects" / slug / "04_MEMORY").mkdir(parents=True)
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    path = save_workflow_output_markdown(
        slug,
        "report",
        "out.md",
        "Dettaglio.",
        header="# Report",
    )
    body = path.read_text(encoding="utf-8")
    assert body.startswith("# Report")


def test_setup_v2_staging_dirs_creates_document_tree(tmp_path: Path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-demo"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    layout = pm.setup_v2_staging_dirs(slug, "doc-001-source.pdf")

    assert layout.root.is_dir()
    assert layout.root.name == "doc-001-source.pdf"
    assert layout.original.is_dir()
    assert layout.extracted_images.is_dir()
    assert layout.extracted_text.is_dir()
    assert layout.extracted_tables.is_dir()
    assert layout.extracted_ocr.is_dir()
    assert layout.extracted_metadata.is_dir()
    assert layout.chunks.is_dir()
    assert layout.enriched.is_dir()
    assert layout.rolling_memory.is_dir()
    assert layout.conflicts_unresolved.is_dir()
    assert layout.conflicts_resolved.is_dir()
    assert layout.audit.is_dir()
    assert layout.map_json.is_file()
    assert layout.conflict_log_json.is_file()

    map_data = json.loads(layout.map_json.read_text(encoding="utf-8"))
    assert map_data["document_id"] == "doc-001-source.pdf"
    assert map_data["pipeline_version"] == "2.0"
    assert "physical_assets" in map_data
    assert "rolling_memory" in map_data


def test_setup_v2_staging_dirs_sanitizes_document_id(tmp_path: Path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-sanitize"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    layout = pm.setup_v2_staging_dirs(slug, "../../evil/id")
    assert ".." not in layout.root.as_posix()
    assert layout.root.name == "id"


def test_setup_v2_project_layout_scaffold(tmp_path: Path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-layout"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    paths = pm.setup_v2_project_layout(slug)

    assert paths["project_meta"].is_dir()
    assert paths["raw_incoming"].is_dir()
    assert paths["raw_quarantine"].is_dir()
    assert paths["staging"].is_dir()
    assert paths["knowledge_canonical"].is_dir()
    assert paths["output_reports"].is_dir()
    assert paths["memory_semantic_memory"].is_file()
    assert paths["log_ingestion"].is_file()
