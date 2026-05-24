"""Test CodeAnalysisWorkflow (LLM mockato)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import engine.project_memory as pm
from workflows.code_analysis import OUTPUT_SUBDIR, CodeAnalysisWorkflow


def test_code_analysis_process_file(tmp_path: Path, monkeypatch) -> None:
    slug = "code-test"
    base = tmp_path / "projects" / slug
    ingest = base / "01_INGEST"
    ingest.mkdir(parents=True)
    (base / "03_OUTPUT").mkdir(parents=True)
    (base / "04_MEMORY").mkdir(parents=True)
    src = ingest / "app.py"
    src.write_text(
        "def run():\n    password = 'hardcoded'\n    eval(user_input)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    logs: list[str] = []
    ctx = {
        "slug": slug,
        "stop_event": None,
        "log_fn": logs.append,
    }

    fake_report = (
        "## Architettura generale\n\n- Entry point `run`.\n\n"
        "## Vulnerabilità e debito tecnico\n\n- `eval` pericoloso.\n\n"
        "## Suggerimenti di refactoring\n\n- Rimuovere eval.\n"
    )
    with patch(
        "workflows.code_analysis.llm_complete",
        return_value=fake_report,
    ):
        result = CodeAnalysisWorkflow().process_file(src, ctx)

    assert result["status"] == "ok"
    assert any("[CODE] Fase" in line for line in logs)
    assert any("Completato" in line for line in logs)

    out = base / "03_OUTPUT" / OUTPUT_SUBDIR / "app.code_review.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "Vulnerabilità" in text
    assert "eval" in text.lower() or "Eval" in text
