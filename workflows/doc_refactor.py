"""
Workflow refactoring documentale — estrazione JSON per chunk + sintesi Gap Report.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from core.ai_tasks import abort_if_stop_requested, get_session_lm_model, llm_complete
from core.chunking_v2 import semantic_chunk
from core.token_budget import resolve_chunk_max_tokens, resolve_token_limits
from engine.project_memory import save_workflow_output
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.workflow_progress import report_llm_start, report_phase, report_save

logger = logging.getLogger(__name__)

WORKFLOW_ID = "doc_refactor"
EXTRACT_TEMP = 0.0
SYNTH_TEMP = 0.05
MAX_EXTRACT_OUTPUT = 800
MAX_SYNTH_OUTPUT = 3000

EXTRACTION_SYSTEM = """Sei un estrattore JSON per analisi documentale DVAMOCLES.
Rispondi SOLO con JSON valido. Nessun testo prima o dopo.
Se un campo è vuoto, usa array vuoto []. Mai inventare."""

SYNTHESIS_SYSTEM = """Sei il redattore del Gap Report DVAMOCLES.
Ricevi estratti strutturati da N chunk e produci un Gap Report ricco,
pronto per aggiornare LAST DOCS. Sezioni: Sintesi, GAP-XX numerati,
Contraddizioni, Handoff IA. Tier 1 (LAST DOCS) vince su tier 2."""


def _parse_json_safe(raw: str, fallback_chunk: int) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) > 1:
            raw = parts[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "facts": [{"claim": raw[:400], "confidence": "low", "section": "?"}],
            "entities": [],
            "gaps_vs_sot": [],
            "open_questions": [],
            "_parse_error": True,
            "_chunk_index": fallback_chunk,
        }


def _synth_budget() -> int:
    try:
        limits = resolve_token_limits(get_session_lm_model())
        return int(limits.context_tokens * 0.65)
    except Exception:
        return 5000


def _build_extraction_prompt(
    chunk,
    n: int,
    total: int,
    filename: str,
    sot_context: str,
) -> str:
    sot_block = f"\nSOT CONTEXT (breve):\n{sot_context[:800]}\n" if sot_context else ""
    code_note = (
        "\nNOTA: il chunk contiene blocchi di codice — estrai specifiche tecniche."
        if chunk.has_code
        else ""
    )
    return (
        f"File: `{filename}` — Chunk {n}/{total}, sezione: {chunk.parent_heading}"
        f"{code_note}\n{sot_block}\n"
        f"TESTO:\n{chunk.text}\n\n"
        'Estrai in JSON: {"facts":[...], "entities":[...], '
        '"gaps_vs_sot":[...], "open_questions":[...]}'
    )


def _build_synthesis_prompt(
    filename: str,
    extracts: list[dict],
    sot_context: str,
) -> str:
    extracts_str = json.dumps(extracts, ensure_ascii=False, indent=1)
    sot_block = f"\nSOT REFERENCE:\n{sot_context[:2000]}\n" if sot_context else ""
    return (
        f"Documento: `{filename}`\n"
        f"Estratti da {len(extracts)} chunk:\n{extracts_str}\n"
        f"{sot_block}\n"
        "Produci il Gap Report completo con: Sintesi, GAP-XX, Contraddizioni, Handoff IA."
    )


def _hierarchical_synthesis(
    extracts: list[dict],
    filename: str,
    sot_context: str,
    log_fn: Callable[[str], None],
) -> str:
    group_size = 5
    mini_reports: list[str] = []
    for i in range(0, len(extracts), group_size):
        group = extracts[i : i + group_size]
        log_fn(f"[REFACTOR] Mini-sintesi chunk {i + 1}–{i + len(group)}")
        prompt = _build_synthesis_prompt(filename, group, sot_context[:1000])
        mini = llm_complete(
            system_prompt=SYNTHESIS_SYSTEM,
            user_message=prompt,
            temperature=SYNTH_TEMP,
            max_tokens=1500,
        )
        mini_reports.append(mini)
    final_prompt = (
        f"Unifica questi {len(mini_reports)} report parziali per `{filename}` "
        f"in un singolo Gap Report consolidato. "
        f"Deduplica GAP simili, mantieni tutti i conflitti.\n\n"
        + "\n\n---\n\n".join(mini_reports)
    )
    return llm_complete(
        system_prompt=SYNTHESIS_SYSTEM,
        user_message=final_prompt,
        temperature=SYNTH_TEMP,
        max_tokens=MAX_SYNTH_OUTPUT,
    )


class DocRefactorWorkflow(BaseWorkflow):
    capabilities = WorkflowCapabilities(
        requires_llm=True,
        requires_rag=True,
        supports_cancel=True,
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        slug = ctx.get("slug")
        if not slug:
            raise ValueError("ctx['slug'] richiesto")
        log_fn: Callable[[str], None] = ctx.get("log_fn") or (lambda _m: None)
        stop_event = ctx.get("stop_event")
        state = ctx.get("orchestrator")
        sot_context: str = ctx.get("sot_context", "")

        try:
            limits = resolve_token_limits(get_session_lm_model())
            chunk_max = resolve_chunk_max_tokens(limits)
        except Exception:
            chunk_max = 1000

        body = file_path.read_text(encoding="utf-8", errors="replace")
        chunks = semantic_chunk(body, max_tokens=chunk_max)
        chunk_count = len(chunks)
        total_phases = chunk_count + 3  # lettura + N extract + sintesi + save

        report_phase(
            ctx,
            tag="REFACTOR",
            phase=1,
            total=total_phases,
            label=f"Lettura e chunking ({chunk_count} chunk, max {chunk_max} tok)",
            file_path=file_path,
        )

        extracts: list[dict] = []
        for i, chunk in enumerate(chunks):
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("DocRefactor interrotto")
            abort_if_stop_requested()
            report_llm_start(
                ctx,
                tag="REFACTOR",
                phase=2 + i,
                total=total_phases,
                file_path=file_path,
                detail=f"Estrazione chunk {i + 1}/{chunk_count}: {chunk.parent_heading[:40]}",
            )
            extract_prompt = _build_extraction_prompt(
                chunk, i + 1, chunk_count, file_path.name, sot_context
            )
            raw = llm_complete(
                system_prompt=EXTRACTION_SYSTEM,
                user_message=extract_prompt,
                temperature=EXTRACT_TEMP,
                max_tokens=MAX_EXTRACT_OUTPUT,
            )
            extract = _parse_json_safe(raw, i)
            extract["_chunk_index"] = i
            extract["_section"] = chunk.parent_heading
            extracts.append(extract)

        synth_phase = 2 + chunk_count
        report_llm_start(
            ctx,
            tag="REFACTOR",
            phase=synth_phase,
            total=total_phases,
            file_path=file_path,
            detail="Sintesi Gap Report",
        )
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("DocRefactor interrotto prima di sintesi")

        synth_prompt = _build_synthesis_prompt(file_path.name, extracts, sot_context)
        from core.token_budget import count_tokens

        if count_tokens(synth_prompt) > _synth_budget():
            log_fn("[REFACTOR] Sintesi gerarchica (prompt troppo grande)")
            report_md = _hierarchical_synthesis(
                extracts, file_path.name, sot_context, log_fn
            )
        else:
            report_md = llm_complete(
                system_prompt=SYNTHESIS_SYSTEM,
                user_message=synth_prompt,
                temperature=SYNTH_TEMP,
                max_tokens=MAX_SYNTH_OUTPUT,
            )

        report_save(
            ctx,
            tag="REFACTOR",
            phase=total_phases,
            total=total_phases,
            file_path=file_path,
            subdir=WORKFLOW_ID,
        )
        out_path = save_workflow_output(
            slug,
            WORKFLOW_ID,
            f"{file_path.stem}_gap.md",
            report_md,
            source_file=file_path.name,
            state=state,
            current_file=file_path.name,
        )
        save_workflow_output(
            slug,
            WORKFLOW_ID,
            f"{file_path.stem}_extracts.json",
            json.dumps(extracts, ensure_ascii=False, indent=2),
            source_file=file_path.name,
        )
        log_fn(f"[REFACTOR] Completato: {file_path.name} → {out_path.name}")
        return {
            "status": "ok",
            "workflow": WORKFLOW_ID,
            "source": file_path.name,
        }
