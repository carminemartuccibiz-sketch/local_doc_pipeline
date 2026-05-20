"""
Split logico Markdown (##) con merge sezioni piccole — meno chunk, un documento per volta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.token_budget import count_tokens


@dataclass(slots=True)
class TextChunk:
    index: int
    label: str
    text: str
    token_estimate: int


def split_markdown_sections(
    body: str,
    *,
    max_tokens: int,
    min_section_tokens: int = 350,
) -> list[TextChunk]:
    """
    Split solo su ## (non ###). Unisce sezioni piccole fino a riempire il budget.
    """
    if count_tokens(body) <= max_tokens:
        return [TextChunk(0, "full", body, count_tokens(body))]

    sections: list[tuple[str, str]] = []
    pattern = re.compile(r"^(##\s+.+)$", re.MULTILINE)
    matches = list(pattern.finditer(body))

    if not matches:
        return _split_by_paragraphs(body, max_tokens=max_tokens, base_label="doc")

    if matches[0].start() > 0:
        preamble = body[: matches[0].start()].strip()
        if preamble:
            sections.append(("preamble", preamble))

    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[start:end].strip()
        if chunk:
            sections.append((title, chunk))

    merged_sections = _merge_small_sections(sections, min_tokens=min_section_tokens)

    out: list[TextChunk] = []
    idx = 0
    for title, text in merged_sections:
        if count_tokens(text) <= max_tokens:
            out.append(TextChunk(idx, title[:80], text, count_tokens(text)))
            idx += 1
            continue
        for sub in _split_by_paragraphs(text, max_tokens=max_tokens, base_label=title[:40]):
            sub.index = idx
            out.append(sub)
            idx += 1

    return _coalesce_chunks(out, max_tokens=max_tokens) if out else [
        TextChunk(0, "full", body[: max_tokens * 4], count_tokens(body))
    ]


def _merge_small_sections(
    sections: list[tuple[str, str]],
    *,
    min_tokens: int,
) -> list[tuple[str, str]]:
    if not sections:
        return sections
    out: list[tuple[str, str]] = []
    buf_label = sections[0][0]
    buf_parts: list[str] = []

    for title, text in sections:
        if not buf_parts:
            buf_label = title
            buf_parts = [text]
            continue
        combined = "\n\n".join(buf_parts + [text])
        if count_tokens(combined) <= min_tokens * 3 and (
            count_tokens(buf_parts[-1]) < min_tokens or count_tokens(text) < min_tokens
        ):
            buf_parts.append(text)
        else:
            out.append((buf_label, "\n\n".join(buf_parts)))
            buf_label = title
            buf_parts = [text]
    if buf_parts:
        out.append((buf_label, "\n\n".join(buf_parts)))
    return out


def _coalesce_chunks(chunks: list[TextChunk], *, max_tokens: int) -> list[TextChunk]:
    """Unisce chunk adiacenti finche restano sotto max_tokens."""
    if len(chunks) <= 1:
        return chunks
    out: list[TextChunk] = []
    buf: list[TextChunk] = []

    def flush() -> None:
        if not buf:
            return
        text = "\n\n".join(c.text for c in buf)
        label = buf[0].label if len(buf) == 1 else f"{buf[0].label} (+{len(buf) - 1} sez.)"
        out.append(TextChunk(len(out), label[:80], text, count_tokens(text)))

    for c in chunks:
        if not buf:
            buf = [c]
            continue
        combined = "\n\n".join(x.text for x in buf) + "\n\n" + c.text
        if count_tokens(combined) <= max_tokens:
            buf.append(c)
        else:
            flush()
            buf = [c]
    flush()
    for i, c in enumerate(out):
        c.index = i
    return out if out else chunks


def _split_by_paragraphs(
    text: str,
    *,
    max_tokens: int,
    base_label: str,
) -> list[TextChunk]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return [TextChunk(0, base_label, text[: max_tokens * 4], count_tokens(text))]

    chunks: list[TextChunk] = []
    buf: list[str] = []
    buf_tokens = 0
    part = 0

    def flush() -> None:
        nonlocal part, buf, buf_tokens
        if not buf:
            return
        joined = "\n\n".join(buf)
        chunks.append(
            TextChunk(
                len(chunks),
                f"{base_label} (part {part})",
                joined,
                count_tokens(joined),
            )
        )
        part += 1
        buf = []
        buf_tokens = 0

    for para in paras:
        pt = count_tokens(para)
        if pt > max_tokens:
            flush()
            step = max(1, max_tokens * 4)
            for off in range(0, len(para), step):
                slice_t = para[off : off + step]
                chunks.append(
                    TextChunk(
                        len(chunks),
                        f"{base_label} (slice)",
                        slice_t,
                        count_tokens(slice_t),
                    )
                )
            continue
        if buf_tokens + pt > max_tokens:
            flush()
        buf.append(para)
        buf_tokens += pt
    flush()
    return chunks
