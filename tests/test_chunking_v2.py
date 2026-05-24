from core.chunking_v2 import BoundaryType, semantic_chunk


def test_code_fence_not_split_across_chunks() -> None:
    body = "# Title\n\n```python\nprint('hello')\nprint('world')\n```\n\nMore text."
    chunks = semantic_chunk(body, max_tokens=50, min_tokens=10)
    combined = "\n".join(c.text for c in chunks)
    assert "```python" in combined
    assert "print('world')" in combined
    assert any(c.has_code or "```" in c.text for c in chunks)


def test_respects_max_tokens_roughly() -> None:
    body = "## A\n\n" + ("word " * 500) + "\n\n## B\n\n" + ("other " * 500)
    chunks = semantic_chunk(body, max_tokens=200, min_tokens=50)
    assert len(chunks) >= 2
