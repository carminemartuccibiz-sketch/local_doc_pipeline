"""Test V2MapManager — CRUD map.json schema §4."""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from engine.v2_map_manager import (
    V2MapManager,
    empty_map_template,
    normalize_map_data,
)


def test_empty_map_template_matches_schema() -> None:
    data = empty_map_template(document_id="DOC_001", source_file="source.pdf")
    assert data["document_id"] == "DOC_001"
    assert data["pipeline_version"] == "2.0"
    assert "images" in data["physical_assets"]
    assert "structured" in data["rolling_memory"]
    assert data["rolling_memory"]["structured"]["entities"] == []
    assert data["rolling_memory"]["structured"]["temporal_markers"] == []


def test_normalize_migrates_legacy_schema_fields() -> None:
    legacy = {
        "version": 1,
        "document_id": "old-doc",
        "created_at": "2026-05-25T10:00:00+00:00",
        "stages": {"extract": {"status": "done"}},
        "chunks": [{"id": "chunk_001", "knowledge_state": {"facts_extracted": True}}],
        "physical_assets": {
            "tables": [{"id": "tbl_001", "path": "extracted/tables/t1.json", "page": 1}],
        },
        "rolling_memory": {
            "structured": {"entities": ["A"], "facts": []},
        },
    }
    out = normalize_map_data(legacy)
    assert out["document_id"] == "old-doc"
    assert out["ingestion_timestamp"] == legacy["created_at"]
    assert out["stages"]["extract"]["status"] == "done"
    assert isinstance(out["physical_assets"]["images"], list)
    assert "rolling_memory" in out
    assert out["rolling_memory"]["structured"]["temporal_markers"] == []
    assert out["chunks"][0]["knowledge_state"]["rolling_context_merged"] is False
    assert out["physical_assets"]["tables"][0]["extraction_confidence"] is None


def test_normalize_migrates_legacy_stub() -> None:
    legacy = {
        "version": 1,
        "document_id": "old-doc",
        "created_at": "2026-05-25T10:00:00+00:00",
        "stages": {"extract": {"status": "done"}},
    }
    out = normalize_map_data(legacy)
    assert out["document_id"] == "old-doc"
    assert out["ingestion_timestamp"] == legacy["created_at"]
    assert out["stages"]["extract"]["status"] == "done"
    assert isinstance(out["physical_assets"]["images"], list)
    assert "rolling_memory" in out


def test_crud_images_chunks_rolling(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="DOC_2026_001")

    mgr.update_metadata({"title": "Arch", "page_count": 10, "ocr_used": True})
    img = mgr.add_image(
        {
            "id": "img_001",
            "path": "extracted/images/img_001.png",
            "page": 4,
            "hash": "abc123",
        }
    )
    assert img["vision_processed"] is False

    chunk = mgr.add_chunk(
        {
            "id": "chunk_001",
            "section": "Overview",
            "pages": [1, 2],
            "char_count": 100,
            "assets": ["img_001"],
        }
    )
    assert chunk["knowledge_state"]["facts_extracted"] is False

    mgr.update_image("img_001", {"linked_chunks": ["chunk_001"], "vision_processed": True})
    mgr.set_chunk_knowledge_state("chunk_001", facts_extracted=True)
    mgr.merge_rolling_structured(
        {
            "entities": ["WorkflowRunner"],
            "facts": [{"text": "Uses Flask", "chunk_id": "chunk_001"}],
        }
    )
    mgr.set_last_merged_chunk("chunk_001")
    mgr.set_compressed_context("Flask orchestrator overview.")

    data = json.loads(map_path.read_text(encoding="utf-8"))
    assert data["metadata"]["title"] == "Arch"
    assert data["physical_assets"]["images"][0]["linked_chunks"] == ["chunk_001"]
    assert data["chunks"][0]["knowledge_state"]["facts_extracted"] is True
    assert data["rolling_memory"]["last_merged_chunk_id"] == "chunk_001"
    assert "WorkflowRunner" in data["rolling_memory"]["structured"]["entities"]


def test_concurrent_chunk_updates(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="doc")
    for i in range(1, 6):
        mgr.add_chunk({"id": f"chunk_{i:03d}", "char_count": i})

    def bump(chunk_id: str, delta: int) -> None:
        local = V2MapManager(map_path)
        c = local.get_chunk(chunk_id)
        assert c is not None
        local.update_chunk(
            chunk_id,
            {"char_count": int(c["char_count"]) + delta},
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(bump, f"chunk_{i:03d}", 10)
            for i in range(1, 6)
        ]
        for f in futures:
            f.result()

    final = V2MapManager(map_path).to_dict()
    counts = {c["id"]: c["char_count"] for c in final["chunks"]}
    assert counts == {f"chunk_{i:03d}": i + 10 for i in range(1, 6)}


def test_async_update_chunk(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="async-doc")
    mgr.add_chunk({"id": "chunk_001", "section": "A"})

    async def run() -> dict:
        m = V2MapManager(map_path)
        return await m.aupdate_chunk("chunk_001", {"section": "B"})

    out = asyncio.run(run())
    assert out["section"] == "B"


def test_duplicate_chunk_raises(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="d")
    mgr.add_chunk({"id": "chunk_001"})
    with pytest.raises(KeyError):
        mgr.add_chunk({"id": "chunk_001"})


def test_os_file_lock_released_on_exception(tmp_path: Path) -> None:
    from engine.v2_map_manager import _os_file_lock

    map_path = tmp_path / "map.json"
    map_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="boom"):
        with _os_file_lock(map_path):
            raise ValueError("boom")
    # second acquire — deadlock se il lock non fosse stato rilasciato
    with _os_file_lock(map_path):
        pass


def test_context_manager_update_chunk_same_thread_no_deadlock(tmp_path: Path) -> None:
    """``__enter__`` tiene ``_file_lock`` — update_chunk deve restare re-entrant (RLock)."""
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="ctx-doc")
    mgr.add_chunk({"id": "chunk_001", "char_count": 1})

    with mgr:
        out = mgr.update_chunk("chunk_001", {"char_count": 99})

    assert out["char_count"] == 99


def test_concurrent_update_chunk_and_metadata_no_deadlock(tmp_path: Path) -> None:
    """Worker paralleli: update_chunk + update_metadata non devono deadlockare."""
    import threading

    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="mix-doc")
    mgr.add_chunk({"id": "chunk_001", "char_count": 0})
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def touch_chunk() -> None:
        local = V2MapManager(map_path)
        try:
            barrier.wait(timeout=5)
            for n in range(20):
                local.update_chunk("chunk_001", {"char_count": n})
        except BaseException as exc:
            errors.append(exc)

    def touch_metadata() -> None:
        local = V2MapManager(map_path)
        try:
            barrier.wait(timeout=5)
            for n in range(20):
                local.update_metadata({"page_count": n})
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=touch_chunk)
    t2 = threading.Thread(target=touch_metadata)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not errors, errors
    assert t1.is_alive() is False and t2.is_alive() is False


def test_map_manager_raises_on_corrupt_json(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    map_path.write_text("{ invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        V2MapManager(map_path)


def test_set_chunks_builds_linked_list(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="link-doc")
    chunks = mgr.set_chunks(
        [
            {"id": "chunk_001", "previous_chunk": None, "next_chunk": None},
            {"id": "chunk_002", "previous_chunk": None, "next_chunk": None},
            {"id": "chunk_003", "previous_chunk": None, "next_chunk": None},
        ]
    )
    assert chunks[0]["previous_chunk"] is None
    assert chunks[0]["next_chunk"] == "chunk_002"
    assert chunks[1]["previous_chunk"] == "chunk_001"
    assert chunks[1]["next_chunk"] == "chunk_003"
    assert chunks[2]["previous_chunk"] == "chunk_002"
    assert chunks[2]["next_chunk"] is None


def test_rebuild_chunk_links_repairs_stale_links(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="repair-doc")
    mgr.set_chunks([{"id": "chunk_001"}, {"id": "chunk_002"}, {"id": "chunk_003"}])

    with mgr._file_lock:
        stale = mgr._data["chunks"]
        stale[1]["previous_chunk"] = "wrong"
        stale[1]["next_chunk"] = "wrong"

    mgr.rebuild_chunk_links()
    fixed = mgr.list_chunks()
    assert fixed[1]["previous_chunk"] == "chunk_001"
    assert fixed[1]["next_chunk"] == "chunk_003"


def test_add_table_includes_extraction_confidence(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    mgr = V2MapManager(map_path, auto_create=True, document_id="tbl-doc")
    tbl = mgr.add_table(
        {
            "id": "tbl_001",
            "path": "extracted/tables/tbl_001.json",
            "page": 2,
            "extraction_confidence": 0.42,
        }
    )
    assert tbl["extraction_confidence"] == 0.42
    default_tbl = mgr.add_table({"id": "tbl_002", "path": "x.json", "page": 1})
    assert default_tbl["extraction_confidence"] is None
