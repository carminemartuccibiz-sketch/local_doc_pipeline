"""Test V2RollingMemory — Stage 4 facts-based rolling context."""
from __future__ import annotations

import json
from pathlib import Path

from engine.project_memory import setup_v2_staging_dirs
from engine.v2_map_manager import V2MapManager
from engine.v2_rolling_memory import RollingState, V2RollingMemory


def test_rolling_state_trim_prioritizes_high_confidence() -> None:
    state = RollingState()
    state.MAX_FACTS = 3
    low_facts = [
        {"claim": f"low-{i}", "section": "s", "confidence": "low"}
        for i in range(3)
    ]
    state.add_extract({"facts": low_facts}, "chunk_001")
    state.add_extract(
        {
            "facts": [{"claim": "high-priority", "section": "s", "confidence": "high"}],
            "entities": ["EntityA"],
            "decisions": ["Use pattern X"],
        },
        "chunk_002",
    )
    claims = [f.claim for f in state.facts]
    assert "high-priority" in claims
    assert len(state.facts) == 3
    assert "EntityA" in state.entities
    assert state.decisions == ["Use pattern X"]


def test_rolling_state_build_context_block() -> None:
    state = RollingState()
    state.add_extract(
        {
            "facts": [{"claim": "API uses Flask", "section": "Arch", "confidence": "high"}],
            "entities": ["Flask"],
        },
        "chunk_001",
    )
    block = state.build_context_block()
    assert "Flask" in block
    assert "API uses Flask" in block


def test_rolling_memory_process_chunk_persists_state(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "roll"
    doc_id = "doc-roll"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs(slug, doc_id)
    mgr = V2MapManager(layout.map_json, auto_create=True, document_id=doc_id)
    mgr.set_chunks(
        [
            {
                "id": "chunk_001",
                "path": "chunks/chunk_001.md",
                "char_count": 10,
            }
        ]
    )
    chunk_path = layout.chunks / "chunk_001.md"
    chunk_path.parent.mkdir(parents=True, exist_ok=True)
    chunk_path.write_text("Flask orchestrator overview.", encoding="utf-8")

    extract_json = json.dumps(
        {
            "facts": [
                {
                    "claim": "Uses Flask",
                    "section": "Overview",
                    "confidence": "high",
                }
            ],
            "entities": ["Flask"],
            "decisions": [],
        }
    )

    rolling = V2RollingMemory(
        layout.rolling_memory,
        map_manager=mgr,
        extractor=lambda **_k: extract_json,
    )
    ctx_block = rolling.process_chunk("chunk_001", chunk_path.read_text(encoding="utf-8"))

    assert "Uses Flask" in ctx_block
    state_path = layout.rolling_memory / "rolling_state.json"
    assert state_path.is_file()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["last_chunk_id"] == "chunk_001"
    assert "Flask" in saved["entities"]

    chunk = mgr.get_chunk("chunk_001")
    assert chunk is not None
    assert chunk["knowledge_state"]["rolling_context_merged"] is True
    rolling_meta = mgr.get_rolling_memory()
    assert rolling_meta["last_merged_chunk_id"] == "chunk_001"
    assert "Flask" in rolling_meta["compressed_context"]


def test_rolling_memory_process_all_chunks(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    slug = "roll-all"
    doc_id = "doc-all"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs(slug, doc_id)
    mgr = V2MapManager(layout.map_json, auto_create=True, document_id=doc_id)
    mgr.set_chunks(
        [
            {"id": "chunk_001", "path": "chunks/chunk_001.md"},
            {"id": "chunk_002", "path": "chunks/chunk_002.md"},
        ]
    )
    layout.chunks.mkdir(parents=True, exist_ok=True)
    (layout.chunks / "chunk_001.md").write_text("Chunk one.", encoding="utf-8")
    (layout.chunks / "chunk_002.md").write_text("Chunk two.", encoding="utf-8")

    calls: list[str] = []

    def fake_extractor(*, text, context, max_tokens):
        calls.append(text[:20])
        return json.dumps(
            {
                "facts": [
                    {
                        "claim": f"Fact from {text[:8]}",
                        "section": "s",
                        "confidence": "medium",
                    }
                ],
                "entities": [],
                "decisions": [],
            }
        )

    count = V2RollingMemory(
        layout.rolling_memory,
        map_manager=mgr,
        extractor=fake_extractor,
    ).process_all_chunks(layout)

    assert count == 2
    assert len(calls) == 2
    data = mgr.to_dict()
    assert data["stages"]["rolling_memory"]["status"] == "completed"


def test_rolling_memory_extractor_failure_is_resilient(tmp_path, monkeypatch) -> None:
    import engine.project_memory as pm

    monkeypatch.setattr(pm, "PROJECTS_ROOT", tmp_path / "projects")
    layout = setup_v2_staging_dirs("rf", "doc")
    mgr = V2MapManager(layout.map_json, auto_create=True, document_id="doc")
    mgr.set_chunks([{"id": "chunk_001", "path": "chunks/chunk_001.md"}])
    (layout.chunks / "chunk_001.md").write_text("text", encoding="utf-8")

    warnings: list[str] = []
    rolling = V2RollingMemory(
        layout.rolling_memory,
        map_manager=mgr,
        extractor=lambda **_k: (_ for _ in ()).throw(RuntimeError("llm down")),
        log_fn=warnings.append,
    )
    rolling.process_chunk("chunk_001", "text")
    assert any("fallita" in w for w in warnings)
    assert (layout.rolling_memory / "rolling_state.json").is_file()
