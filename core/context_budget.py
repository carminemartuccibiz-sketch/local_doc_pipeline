"""
Gestione budget contesto per modelli locali (troncatura + avvisi).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ContextBundle:
    sot_text: str
    raw_text: str
    report_text: str
    warnings: list[str]


def truncate_middle(text: str, max_chars: int, label: str) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    half = max_chars // 2
    truncated = (
        text[:half]
        + f"\n\n_[… {label} troncato: {len(text) - max_chars} caratteri omessi …]_\n\n"
        + text[-half:]
    )
    return truncated, True


def build_context_bundle(
    *,
    sot_parts: list[tuple[str, str]],
    raw_label: str,
    raw_body: str,
    existing_report: str,
    max_sot_chars: int,
    max_raw_chars: int,
    max_report_chars: int,
) -> ContextBundle:
    warnings: list[str] = []

    sot_blocks: list[str] = []
    total = 0
    per_file_budget = max(2000, max_sot_chars // max(len(sot_parts), 1))

    for rel, body in sot_parts:
        chunk = body
        if len(chunk) > per_file_budget:
            chunk, _ = truncate_middle(chunk, per_file_budget, rel)
            warnings.append(f"SOT troncato per file: {rel}")
        if total + len(chunk) > max_sot_chars:
            remaining = max_sot_chars - total
            if remaining > 500:
                chunk = chunk[:remaining]
            else:
                warnings.append("Budget SOT esaurito — alcuni file SOT omessi")
                break
        sot_blocks.append(f"### SOT: {rel}\n\n{chunk}")
        total += len(chunk)

    sot_text = "\n\n---\n\n".join(sot_blocks) if sot_blocks else "_Nessun documento SOT caricato._"

    raw_text, raw_trunc = truncate_middle(raw_body, max_raw_chars, raw_label)
    if raw_trunc:
        warnings.append(f"Target troncato: {raw_label}")

    report_text, rep_trunc = truncate_middle(
        existing_report or "_Nessun report precedente._",
        max_report_chars,
        "Gap_Report",
    )
    if rep_trunc:
        warnings.append("Gap Report precedente troncato per integrazione")

    if warnings:
        for w in warnings:
            logger.warning("Contesto: %s", w)

    return ContextBundle(
        sot_text=sot_text,
        raw_text=raw_text,
        report_text=report_text,
        warnings=warnings,
    )
