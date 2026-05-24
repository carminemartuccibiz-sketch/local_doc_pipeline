"""
Semantic markdown chunking — heading tree, atomic code fences/tables, heading overlap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.token_budget import count_tokens

_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
_TABLE_BLOCK_RE = re.compile(
    r"(?:^|\n)(\|[^\n]+\|\n\|[-:\s|]+\|\n(?:\|[^\n]+\|\n?)+)",
    re.MULTILINE,
)


class BoundaryType(Enum):
    H1 = 0
    H2 = 1
    H3 = 2
    CODE_FENCE = 3
    TABLE = 4
    PARAGRAPH = 5
    SENTENCE = 6


@dataclass
class SemanticChunk:
    index: int
    text: str
    token_estimate: int
    boundary_type: BoundaryType
    parent_heading: str
    has_code: bool
    has_table: bool
    cross_refs: list[str]


def _detect_cross_refs(text: str, all_headings: list[str]) -> list[str]:
    refs: list[str] = []
    low = text.lower()
    for h in all_headings:
        if not h:
            continue
        if h.lower() in low and h not in text[: len(h) + 2]:
            refs.append(h)
    return refs


def _resolve_atomic_placeholders(text: str, placeholders: list[dict[str, Any]]) -> str:
    def repl(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        if 0 <= idx < len(placeholders):
            return placeholders[idx]["text"]
        return m.group(0)

    return re.sub(r"<<<ATOMIC_(\d+)>>>", repl, text)


def _extract_atomic_blocks(body: str) -> tuple[str, list[dict[str, Any]]]:
    """Replace code fences and tables with placeholders; return sections metadata."""
    placeholders: list[dict[str, Any]] = []
    work = body

    def _repl_code(m: re.Match[str]) -> str:
        idx = len(placeholders)
        placeholders.append(
            {
                "text": m.group(0),
                "atomic": True,
                "boundary_type": BoundaryType.CODE_FENCE,
                "has_code": True,
                "has_table": False,
            }
        )
        return f"\n<<<ATOMIC_{idx}>>>\n"

    work = _CODE_FENCE_RE.sub(_repl_code, work)

    def _repl_table(m: re.Match[str]) -> str:
        idx = len(placeholders)
        placeholders.append(
            {
                "text": m.group(1).strip(),
                "atomic": True,
                "boundary_type": BoundaryType.TABLE,
                "has_code": False,
                "has_table": True,
            }
        )
        return f"\n<<<ATOMIC_{idx}>>>\n"

    work = _TABLE_BLOCK_RE.sub(_repl_table, work)
    return work, placeholders


def _extract_heading_tree(body: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_heading = ""
    current_h1 = ""
    current_h2 = ""
    buf: list[str] = []

    def flush_paragraph() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        if text.startswith("<<<ATOMIC_") and text.endswith(">>>"):
            try:
                idx = int(text.replace("<<<ATOMIC_", "").replace(">>>", "").strip())
                sections.append({"placeholder_idx": idx})
            except ValueError:
                sections.append(
                    {
                        "text": text,
                        "atomic": False,
                        "heading": current_heading,
                        "h1": current_h1,
                        "h2": current_h2,
                    }
                )
            return
        sections.append(
            {
                "text": text,
                "atomic": False,
                "heading": current_heading,
                "h1": current_h1,
                "h2": current_h2,
            }
        )

    for line in body.splitlines():
        hm = _HEADING_RE.match(line)
        if hm:
            flush_paragraph()
            level = len(hm.group(1))
            title = hm.group(2).strip()
            if level == 1:
                current_h1 = title
                current_h2 = ""
                current_heading = title
            elif level == 2:
                current_h2 = title
                current_heading = title
            else:
                current_heading = title
            sections.append(
                {
                    "text": line,
                    "atomic": False,
                    "heading": current_heading,
                    "h1": current_h1,
                    "h2": current_h2,
                    "is_heading_line": True,
                }
            )
            continue
        buf.append(line)
    flush_paragraph()
    return sections


def _flush_chunk(
    chunks: list[SemanticChunk],
    parts: list[str],
    heading: str,
    model_hint: str,
    boundary: BoundaryType,
    *,
    has_code: bool = False,
    has_table: bool = False,
    all_headings: list[str],
) -> None:
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return
    if "```" in text:
        has_code = True
    chunks.append(
        SemanticChunk(
            index=len(chunks),
            text=text,
            token_estimate=count_tokens(text, model_hint=model_hint),
            boundary_type=boundary,
            parent_heading=heading,
            has_code=has_code,
            has_table=has_table,
            cross_refs=_detect_cross_refs(text, all_headings),
        )
    )


def semantic_chunk(
    body: str,
    *,
    max_tokens: int,
    model_hint: str = "cl100k_base",
    min_tokens: int = 100,
    overlap_strategy: str = "heading_context",
) -> list[SemanticChunk]:
    """
    Pack markdown into token-budget chunks; never split atomic code/table blocks.
    """
    del overlap_strategy  # reserved: only heading_context implemented
    work, atomic_placeholders = _extract_atomic_blocks(body)
    sections = _extract_heading_tree(work)

    all_headings: list[str] = []
    for s in sections:
        h = s.get("heading") or ""
        if h and h not in all_headings:
            all_headings.append(h)

    chunks: list[SemanticChunk] = []
    current_parts: list[str] = []
    current_tokens = 0
    current_heading = ""
    current_boundary = BoundaryType.PARAGRAPH
    chunk_has_code = False
    chunk_has_table = False

    def flush_current() -> None:
        nonlocal current_parts, current_tokens, chunk_has_code, chunk_has_table
        if not current_parts:
            return
        _flush_chunk(
            chunks,
            current_parts,
            current_heading,
            model_hint,
            current_boundary,
            has_code=chunk_has_code,
            has_table=chunk_has_table,
            all_headings=all_headings,
        )
        overlap_line = (
            f"[Contesto: {current_heading}]\n" if current_heading else ""
        )
        current_parts = [overlap_line] if overlap_line else []
        current_tokens = (
            count_tokens(overlap_line, model_hint=model_hint) if overlap_line else 0
        )
        chunk_has_code = False
        chunk_has_table = False

    for section in sections:
        if "placeholder_idx" in section:
            ph = atomic_placeholders[section["placeholder_idx"]]
            sec_text = ph["text"]
            sec_tokens = count_tokens(sec_text, model_hint=model_hint)
            btype = ph["boundary_type"]
            if current_tokens + sec_tokens > max_tokens and current_parts:
                if current_tokens >= min_tokens:
                    flush_current()
            current_parts.append(sec_text)
            current_tokens += sec_tokens
            current_boundary = btype
            chunk_has_code = chunk_has_code or ph.get("has_code", False)
            chunk_has_table = chunk_has_table or ph.get("has_table", False)
            continue

        sec_text = section.get("text", "")
        if not sec_text:
            continue
        if "<<<ATOMIC_" in sec_text:
            sec_text = _resolve_atomic_placeholders(sec_text, atomic_placeholders)
            if section.get("placeholder_idx") is None:
                ph_idx = section.get("placeholder_idx")
                if ph_idx is None and atomic_placeholders:
                    for i, ph in enumerate(atomic_placeholders):
                        if ph["text"] in sec_text:
                            section = {
                                **section,
                                "placeholder_idx": i,
                            }
                            break
        sec_tokens = count_tokens(sec_text, model_hint=model_hint)
        if section.get("heading"):
            current_heading = section["heading"]

        if current_tokens + sec_tokens > max_tokens and current_tokens >= min_tokens:
            flush_current()

        current_parts.append(sec_text)
        current_tokens += sec_tokens

    if current_parts:
        _flush_chunk(
            chunks,
            current_parts,
            current_heading,
            model_hint,
            current_boundary,
            has_code=chunk_has_code,
            has_table=chunk_has_table,
            all_headings=all_headings,
        )

    for i, ch in enumerate(chunks):
        ch.index = i
    return chunks
