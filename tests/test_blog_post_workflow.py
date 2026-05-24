"""Test BlogPostWorkflow (LLM mockato)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import engine.project_memory as pm
from workflows.blog_post import BlogPostWorkflow, OUTPUT_SUBDIR


def test_blog_post_process_file(tmp_path: Path, monkeypatch) -> None:
    slug = "blog-test"
    base = tmp_path / "projects" / slug
    ingest = base / "01_INGEST"
    ingest.mkdir(parents=True)
    (base / "03_OUTPUT").mkdir(parents=True)
    (base / "04_MEMORY").mkdir(parents=True)
    src = ingest / "note.txt"
    src.write_text(
        "Prodotto X riduce i tempi di deploy del 40%.\nBeneficio: meno errori in produzione.",
        encoding="utf-8",
    )
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    ctx = {
        "slug": slug,
        "stop_event": None,
        "log_fn": lambda _m: None,
    }

    with patch(
        "workflows.blog_post.llm_complete",
        return_value="# Deploy più veloce\n\n- Meno errori\n- Team più produttivo",
    ):
        result = BlogPostWorkflow().process_file(src, ctx)

    assert result["status"] == "ok"
    out = base / "03_OUTPUT" / OUTPUT_SUBDIR / "note.md"
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "Deploy più veloce" in text
