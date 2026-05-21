"""
Frontmatter YAML obbligatorio per i Gap Report (categoria SPEC).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _yaml_quote(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")


def build_gap_frontmatter(
    *,
    source_file: str,
    sot_references: list[str] | None = None,
    title: str | None = None,
    status: str = "gap_identified",
    chunk_label: str | None = None,
    chunks_total: int | None = None,
    chunk_labels: list[str] | None = None,
    sot_tiers: str | None = None,
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = Path(source_file.replace("\\", "/")).name
    tit = title or f"Gap Report — {name}"
    lines = [
        "---",
        "author: DVAMOCLES",
        "category: SPEC",
        f"source_file: {_yaml_quote(source_file)}",
        f"status: {status}",
        f"title: \"{_yaml_quote(tit)}\"",
        f"generated_at: \"{ts}\"",
    ]
    if chunk_label:
        lines.append(f"chunk: \"{_yaml_quote(chunk_label)}\"")
    if chunks_total is not None and chunks_total > 0:
        mode = "chunked" if chunks_total > 1 else "single_part"
        lines.append(f"chunking_total: {chunks_total}")
        lines.append(f"chunking_mode: {mode}")
        if chunk_labels:
            lines.append("chunking_labels:")
            for lab in chunk_labels[:50]:
                lines.append(f"  - \"{_yaml_quote(lab)}\"")
    if sot_references:
        lines.append("sot_reference:")
        for ref in sot_references[:40]:
            lines.append(f"  - \"{_yaml_quote(ref)}\"")
    lines.append("report_type: gap_analysis")
    lines.append("report_purpose: ai_document_update_handoff")
    if sot_tiers:
        lines.append(f"sot_tiers: \"{_yaml_quote(sot_tiers)}\"")
    lines.append(
        "ai_usage: \"Allegare a Claude/GPT con LAST DOCS aperti. "
        "Usare sezioni Azione di redazione e Handoff IA. Tier 1 vince su tier 2.\""
    )
    lines.extend(["---", ""])
    return "\n".join(lines)


def ensure_spec_document(
    body: str,
    *,
    source_file: str,
    sot_references: list[str] | None = None,
    title: str | None = None,
    chunk_label: str | None = None,
    chunks_total: int | None = None,
    chunk_labels: list[str] | None = None,
    sot_tiers: str | None = None,
) -> str:
    if body.lstrip().startswith("---"):
        return body
    fm = build_gap_frontmatter(
        source_file=source_file,
        sot_references=sot_references,
        title=title,
        chunk_label=chunk_label,
        chunks_total=chunks_total,
        chunk_labels=chunk_labels,
        sot_tiers=sot_tiers,
    )
    return fm + body.lstrip()
