"""Test V2ChunkingManager — physical semantic chunking."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.project_memory import setup_v2_staging_dirs
from engine.v2_chunking_manager import V2ChunkingManager
from engine.v2_map_manager import V2MapManager
from engine.v2_physical_extractor import RAW_EXTRACTED_NAME


def _write_raw_extracted(layout, body: str) -> Path:
    path = layout.extracted_text / RAW_EXTRACTED_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_v2_chunking_writes_files_and_linked_list(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "v2-chunk"
    doc_id = "doc-chunk-001"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")

    layout = setup_v2_staging_dirs(slug, doc_id)
    _write_raw_extracted(
        layout,
        "# Doc\n\n## Page 1\n\n"
        "Intro paragraph with [IMG_REF:img_001] reference.\n\n"
        "## Architecture\n\n"
        + ("semantic content word " * 80)
        + "\n\n## Page 2\n\n"
        + ("more content token " * 80),
    )

    mgr = V2MapManager(layout.map_json)
    mgr.add_image(
        {
            "id": "img_001",
            "path": "extracted/images/img_001.png",
            "page": 1,
            "hash": "abc",
        }
    )

    result = V2ChunkingManager(
        layout,
        map_manager=mgr,
        max_tokens=120,
        min_tokens=20,
    ).chunk_raw_extract()

    assert result.chunk_count >= 2
    assert (layout.chunks / "chunk_001.md").is_file()
    assert (layout.chunks / "chunk_002.md").is_file()
    assert "[IMG_REF:img_001]" in (layout.chunks / "chunk_001.md").read_text(encoding="utf-8")

    data = mgr.to_dict()
    chunks = data["chunks"]
    assert len(chunks) == result.chunk_count
    assert chunks[0]["id"] == "chunk_001"
    assert chunks[0]["previous_chunk"] is None
    assert chunks[0]["next_chunk"] == "chunk_002"
    assert chunks[-1]["next_chunk"] is None
    assert chunks[-1]["previous_chunk"] == chunks[-2]["id"]
    assert "img_001" in chunks[0]["assets"]
    assert chunks[0]["path"] == "chunks/chunk_001.md"

    img = mgr.get_image("img_001")
    assert img is not None
    assert "chunk_001" in img["linked_chunks"]
    assert data["stages"]["physical_chunking"]["status"] == "completed"


def test_v2_chunking_missing_raw_raises(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs("empty-chunk", "no-raw")
    with pytest.raises(FileNotFoundError):
        V2ChunkingManager(layout, max_tokens=200).chunk_raw_extract()


def test_v2_chunking_preserves_code_fence(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs("code-chunk", "doc-code")
    _write_raw_extracted(
        layout,
        "## Sample\n\n```python\nprint('ok')\n```\n\nTail text.",
    )

    result = V2ChunkingManager(layout, max_tokens=500, min_tokens=10).chunk_raw_extract()
    combined = "".join(
        (layout.chunks / p.name).read_text(encoding="utf-8")
        for p in sorted(layout.chunks.glob("chunk_*.md"))
    )
    assert "```python" in combined
    assert "print('ok')" in combined
    assert result.chunk_count >= 1
