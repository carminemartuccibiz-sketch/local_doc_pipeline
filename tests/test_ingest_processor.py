"""Test preflight ingest (lettura + token budget) senza LLM."""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.ingest_processor import (
    IngestBudgetError,
    IngestCallBudget,
    IngestReadError,
    _build_chunks_with_overlap,
    _extract_structural_overlap,
    _fence_spans,
    _preflight_llm_payload,
    _preflight_whole_document,
    read_document_safe,
)


def test_read_document_safe_utf8(tmp_path: Path) -> None:
    f = tmp_path / "ok.md"
    f.write_text("# Ciao\n\nTesto.", encoding="utf-8")
    body = read_document_safe(f)
    assert "Ciao" in body


def test_read_document_safe_empty(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    with pytest.raises(IngestReadError, match="vuoto"):
        read_document_safe(f)


def test_preflight_document_too_large() -> None:
    budget = IngestCallBudget(
        model_hint="cl100k_base",
        context_tokens=4096,
        chunk_max_tokens=1200,
        output_reserve=800,
        max_document_tokens=100,
        max_chunks=50,
    )
    body = "parola " * 500
    with pytest.raises(IngestBudgetError, match="token totali"):
        _preflight_whole_document(body, Path("big.md"), budget, None)


def test_preflight_llm_truncates_huge_chunk() -> None:
    budget = IngestCallBudget(
        model_hint="cl100k_base",
        context_tokens=8192,
        chunk_max_tokens=1200,
        output_reserve=800,
        max_document_tokens=4000,
        max_chunks=100,
    )
    huge = "x" * 200_000
    out = _preflight_llm_payload(
        system_prompt="system",
        user_message=huge,
        budget=budget,
        log_fn=None,
        label="test",
        max_output_tokens=800,
    )
    assert len(out) < len(huge)


def test_extract_structural_overlap_keeps_code_fence_intact() -> None:
    prev = (
        "Intro paragrafo uno.\n\n"
        "```python\n"
        "def foo():\n"
        "    return 1\n"
        "```\n\n"
        "Chiusura dopo il blocco."
    )
    tail = _extract_structural_overlap(prev, 80)
    assert "```" not in tail or tail.count("```") % 2 == 0
    if "```" in tail:
        assert "def foo" in tail or tail.startswith("```")


def test_build_chunks_overlap_on_paragraph_boundary() -> None:
    body = (
        "## Sezione A\n\n"
        + ("Paragrafo lungo con molte parole. " * 40)
        + "\n\n"
        "## Sezione B\n\n"
        + ("Altro paragrafo denso. " * 40)
    )
    chunks, metas = _build_chunks_with_overlap(body, max_tokens=120, overlap_chars=200)
    assert len(chunks) >= 2
    second = chunks[1].text
    assert "[...contesto dal blocco precedente...]" in second
    assert metas[1].overlap_with_prev_chars > 0


def test_fence_spans_detects_block() -> None:
    text = "prima\n```js\nx=1\n```\ndopo"
    spans = _fence_spans(text)
    assert len(spans) == 1
    assert "x=1" in text[spans[0].start : spans[0].end]
