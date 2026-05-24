"""Tests MinHash dedup (Task B1)."""
from __future__ import annotations

from core.dedup import (
    jaccard_from_minhash,
    minhash_dedup,
    minhash_signature,
    similarity_threshold,
    text_similarity,
)
from engine.project_store import list_ingest_sources


def test_jaccard_identical_text_is_one() -> None:
    text = "hello world " * 50
    a = minhash_signature(text)
    b = minhash_signature(text)
    assert jaccard_from_minhash(a, b) >= 0.99


def test_jaccard_different_text_low() -> None:
    a = minhash_signature("alpha beta gamma delta epsilon " * 20)
    b = minhash_signature("uno due tre quattro cinque " * 20)
    assert text_similarity(
        "alpha beta gamma delta epsilon " * 20,
        "uno due tre quattro cinque " * 20,
    ) < similarity_threshold()


def test_text_similarity_near_duplicate_high() -> None:
    base = "Documento tecnico con molte parole ripetute. " * 30
    near = base + " piccola variazione."
    assert text_similarity(base, near) >= similarity_threshold()


def test_minhash_dedup_removes_near_duplicate_chunks() -> None:
    class C:
        def __init__(self, text: str, index: int = 0):
            self.text = text
            self.index = index

    base = "Documento tecnico con molte parole ripetute. " * 30
    near = base + " piccola variazione."
    far = "Completamente diverso " * 40
    out = minhash_dedup([C(base, 0), C(near, 1), C(far, 2)])
    assert len(out) == 2


def test_list_ingest_sources_skips_near_duplicate_in_same_pass() -> None:
    from engine import project_store

    slug = "dedup-demo"
    project_dir = project_store.PROJECTS_ROOT / slug / "01_INGEST"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_store.PROJECTS_ROOT / slug / "04_MEMORY").mkdir(parents=True, exist_ok=True)

    original = "Titolo unico\n\n" + ("Contenuto condiviso identico. " * 80)
    variant = original + "\n\nNota marginale."
    (project_dir / "original.md").write_text(original, encoding="utf-8")
    (project_dir / "copy.md").write_text(variant, encoding="utf-8")

    logs: list[str] = []
    sources = list_ingest_sources(slug, skip_completed=False, log_fn=logs.append)
    assert len(sources) == 1
    assert any("Documento già presente" in m for m in logs)
