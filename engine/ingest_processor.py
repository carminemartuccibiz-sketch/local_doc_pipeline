"""
engine/ingest_processor.py — Sliding Window Context (Task 4 blueprint).

Processo per file:
  1. Lettura (extract_plain) + chunking con overlap fisico
  2. Salvataggio in projects/<slug>/01_INGEST/<stem>/
  3. Loop analisi LLM con condensato del chunk precedente
  4. Append su analysis.md chunk per chunk
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.chunking import TextChunk, split_markdown_sections
from core.converters import extract_plain
from core.file_io import atomic_write_json
from core.token_budget import count_tokens, resolve_chunk_max_tokens

logger = logging.getLogger(__name__)

# ─── Strutture dati ────────────────────────────────────────────────────────────


@dataclass
class ChunkMeta:
    index: int
    label: str
    char_start: int
    char_end: int
    token_estimate: int
    overlap_with_prev_chars: int = 0
    overlap_with_next_chars: int = 0


@dataclass
class ChunkAnalysis:
    index: int
    label: str
    analysis: str  # Analisi estesa dell'AI
    condensed: str  # Sommario (~150 token) per il chunk successivo


@dataclass
class FileIngestResult:
    source_file: str
    total_chunks: int
    analyses: list[ChunkAnalysis] = field(default_factory=list)
    final_summary: str = ""


# ─── Chunking con overlap ──────────────────────────────────────────────────────

OVERLAP_CHARS = 400  # ~100 token di sovrapposizione tra chunk adiacenti

ANALYZE_SYSTEM_PROMPT = """Sei un analista documentale. Ti viene fornito un frammento di documento
(con eventuale contesto dal frammento precedente).

Produci:
1. **ANALISI**: descrizione dettagliata del contenuto (concetti, strutture, dati rilevanti).
2. **CONDENSATO**: sintesi in max 3 frasi dense (servirà come contesto per il frammento successivo).

Formato obbligatorio:
## ANALISI
[testo]

## CONDENSATO
[max 3 frasi]"""

CONDENSE_SYSTEM_PROMPT = """Estrai un condensato di massimo 3 frasi dall'analisi fornita.
Solo il condensato, nessun'altra parola."""


def _locate_chunk_in_body(body: str, chunk_text: str, search_from: int) -> int:
    if not chunk_text:
        return search_from
    probe = chunk_text[: min(120, len(chunk_text))]
    pos = body.find(probe, search_from)
    if pos >= 0:
        return pos
    pos = body.find(chunk_text[: min(40, len(chunk_text))].strip(), search_from)
    return pos if pos >= 0 else search_from


def _format_chunked_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _overlap_tokens_estimate(overlap_chars: int, *, sample: str = "") -> int:
    if sample:
        return max(1, count_tokens(sample[:overlap_chars]))
    return max(1, overlap_chars // 4)


def _chunk_meta_to_json(meta: ChunkMeta, *, total: int) -> dict:
    row: dict = {
        "index": meta.index,
        "label": meta.label,
        "char_start": meta.char_start,
        "char_end": meta.char_end,
        "token_estimate": meta.token_estimate,
    }
    if meta.overlap_with_prev_chars > 0:
        row["overlap_with_prev_chars"] = meta.overlap_with_prev_chars
    if meta.overlap_with_next_chars > 0 and (meta.index < total - 1 or total == 1):
        row["overlap_with_next_chars"] = meta.overlap_with_next_chars
    return row


def _build_chunks_with_overlap(
    body: str,
    *,
    max_tokens: int,
    overlap_chars: int = OVERLAP_CHARS,
) -> tuple[list[TextChunk], list[ChunkMeta]]:
    """
    Genera chunk logici (split su ##) e aggiunge overlap fisico
    prendendo la coda del chunk precedente e incollandola all'inizio del successivo.
    """
    base_chunks = split_markdown_sections(body, max_tokens=max_tokens)

    if not base_chunks:
        base_chunks = [TextChunk(0, "full", body, count_tokens(body))]

    if len(base_chunks) <= 1:
        chunk = base_chunks[0]
        tok = count_tokens(chunk.text)
        meta = ChunkMeta(
            0,
            chunk.label,
            0,
            len(body),
            tok,
            overlap_with_next_chars=min(overlap_chars, len(chunk.text)),
        )
        return [TextChunk(0, chunk.label, chunk.text, tok)], [meta]

    span_start: list[int] = []
    span_end: list[int] = []
    search_at = 0
    for chunk in base_chunks:
        start = _locate_chunk_in_body(body, chunk.text, search_at)
        end = min(len(body), start + len(chunk.text))
        span_start.append(start)
        span_end.append(end)
        search_at = max(search_at, end - overlap_chars) if overlap_chars else end

    enriched_chunks: list[TextChunk] = []
    metas: list[ChunkMeta] = []

    for i, chunk in enumerate(base_chunks):
        overlap_prefix = ""
        overlap_prev_chars = 0
        if i > 0:
            prev_text = base_chunks[i - 1].text
            tail = (
                prev_text[-overlap_chars:]
                if len(prev_text) > overlap_chars
                else prev_text
            )
            overlap_prefix = f"[...contesto dal blocco precedente...]\n{tail}\n\n"
            overlap_prev_chars = len(tail)

        enriched_text = overlap_prefix + chunk.text
        enriched_chunks.append(
            TextChunk(
                index=i,
                label=chunk.label,
                text=enriched_text,
                token_estimate=count_tokens(enriched_text),
            )
        )

        if i > 0:
            prev_span_len = span_end[i - 1] - span_start[i - 1]
            char_start = max(0, span_end[i - 1] - min(overlap_chars, prev_span_len))
        else:
            char_start = span_start[i]

        char_end = span_end[i]
        if i == len(base_chunks) - 1:
            char_end = max(char_end, len(body))

        overlap_next = (
            min(overlap_chars, len(chunk.text)) if i < len(base_chunks) - 1 else 0
        )

        metas.append(
            ChunkMeta(
                index=i,
                label=chunk.label,
                char_start=char_start,
                char_end=char_end,
                token_estimate=count_tokens(enriched_text),
                overlap_with_prev_chars=overlap_prev_chars,
                overlap_with_next_chars=overlap_next,
            )
        )

    return enriched_chunks, metas


# ─── Sliding Window Loop ───────────────────────────────────────────────────────


def _extract_condensed(analysis_text: str, *, llm_fn: Callable[..., str]) -> str:
    """Estrae il condensato dall'analisi. Prima parsing ## CONDENSATO, poi fallback LLM."""
    if "## CONDENSATO" in analysis_text:
        parts = analysis_text.split("## CONDENSATO", 1)
        candidate = parts[1].strip().split("##")[0].strip()
        if len(candidate) > 30:
            return candidate

    return llm_fn(
        system_prompt=CONDENSE_SYSTEM_PROMPT,
        user_message=analysis_text[:3000],
        temperature=0.05,
        max_tokens=200,
    )


def _resolve_file_dir(
    file_path: Path,
    *,
    file_dir: Path | None,
    project_ingest_dir: Path | None,
) -> Path:
    if file_dir is not None:
        return Path(file_dir)
    if project_ingest_dir is not None:
        return Path(project_ingest_dir) / file_path.stem
    raise ValueError("Specificare file_dir oppure project_ingest_dir")


def sliding_window_analyze(
    file_path: Path,
    file_dir: Path | None = None,
    llm_fn: Callable[..., str] | None = None,
    stop_event=None,
    log_fn: Callable[[str], None] | None = None,
    max_tokens_per_chunk: int = 1200,
    overlap_chars: int = OVERLAP_CHARS,
    *,
    project_ingest_dir: Path | None = None,
    skip_llm: bool = False,
    **kwargs: object,
) -> FileIngestResult:
    """
    Processo completo per un singolo file (Task 4).

    Parametri:
      file_path — file sorgente
      file_dir — cartella ingest (es. projects/<slug>/01_INGEST/<stem>/)
      project_ingest_dir — alternativa: root 01_INGEST; usa <stem>/ come file_dir
      llm_fn — tipicamente core.ai_tasks.llm_complete
      stop_event — threading.Event (kill switch orchestrator)
      log_fn — opzionale, messaggi verso UI/SSE
    """
    if llm_fn is None or stop_event is None:
        raise TypeError("llm_fn e stop_event sono obbligatori")

    file_path = Path(file_path)
    resolved_dir = _resolve_file_dir(
        file_path,
        file_dir=file_dir,
        project_ingest_dir=project_ingest_dir,
    )

    def log(msg: str) -> None:
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    # ── 1. Lettura e chunking ─────────────────────────────────────────────
    log(f"[INGEST] Lettura: {file_path.name}")
    body = extract_plain(file_path)
    if not body.strip():
        raise ValueError(f"Nessun testo estratto da {file_path}")

    chunks, metas = _build_chunks_with_overlap(
        body,
        max_tokens=max_tokens_per_chunk,
        overlap_chars=overlap_chars,
    )
    log(f"[INGEST] {len(chunks)} chunk (overlap {overlap_chars} char)")

    # ── 2. Cartella isolata per questo file ──────────────────────────────
    resolved_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, resolved_dir / f"original{file_path.suffix}")

    for chunk in chunks:
        # Blueprint: chunk_001.txt (1-based); index in chunks.json resta 0-based
        chunk_path = resolved_dir / f"chunk_{chunk.index + 1:03d}.txt"
        chunk_path.write_text(chunk.text, encoding="utf-8", newline="\n")

    source_md5 = hashlib.md5(body.encode("utf-8", errors="replace")).hexdigest()
    chunks_json = {
        "source_file": file_path.name,
        "source_md5": source_md5,
        "chunked_at": _format_chunked_at(),
        "chunk_strategy": "markdown_sections_overlap",
        "overlap_tokens": _overlap_tokens_estimate(
            overlap_chars, sample=body[:overlap_chars]
        ),
        "overlap_chars": overlap_chars,
        "total_chunks": len(chunks),
        "chunks": [_chunk_meta_to_json(m, total=len(metas)) for m in metas],
    }
    atomic_write_json(resolved_dir / "chunks.json", chunks_json)
    log(f"[INGEST] Struttura salvata: {resolved_dir}")

    if skip_llm:
        return FileIngestResult(
            source_file=str(file_path),
            total_chunks=len(chunks),
            analyses=[],
            final_summary="",
        )

    # ── 3. Sliding Window Analysis ───────────────────────────────────────
    analysis_doc_path = resolved_dir / "analysis.md"
    analyses: list[ChunkAnalysis] = []
    prev_condensed = ""

    for chunk in chunks:
        if stop_event.is_set():
            log(f"[STOP] Interrotto a chunk {chunk.index}/{len(chunks)}")
            raise InterruptedError("Sliding window interrotto da Kill Switch")

        log(f"[ANALYZE] Chunk {chunk.index + 1}/{len(chunks)}: {chunk.label}")

        context_block = ""
        if prev_condensed:
            context_block = (
                f"\n---\n**Contesto dal blocco precedente (condensato):**\n"
                f"{prev_condensed}\n---\n\n"
            )

        user_msg = (
            f"# Documento: `{file_path.name}`\n"
            f"# Frammento {chunk.index + 1} di {len(chunks)}: `{chunk.label}`\n"
            f"{context_block}"
            f"## Testo del frammento\n\n{chunk.text}"
        )

        raw_analysis = llm_fn(
            system_prompt=ANALYZE_SYSTEM_PROMPT,
            user_message=user_msg,
            temperature=0.07,
            max_tokens=800,
        )

        condensed = _extract_condensed(raw_analysis, llm_fn=llm_fn)
        prev_condensed = condensed

        analyses.append(
            ChunkAnalysis(
                index=chunk.index,
                label=chunk.label,
                analysis=raw_analysis,
                condensed=condensed,
            )
        )

        section = (
            f"\n\n---\n\n"
            f"## Chunk {chunk.index + 1}/{len(chunks)}: {chunk.label}\n\n"
            f"{raw_analysis.strip()}\n\n"
            f"**[CONDENSATO per il prossimo chunk]:** {condensed}\n"
        )
        with analysis_doc_path.open("a", encoding="utf-8", newline="\n") as f:
            if chunk.index == 0:
                f.write(
                    f"---\nsource: {file_path.name}\ngenerated_at: auto\n---\n\n"
                    f"# Analisi sliding window: `{file_path.name}`\n\n"
                    f"_Documento di tracciamento aggiornato chunk per chunk._\n"
                )
            f.write(section)

        log(
            f"[ANALYZE] Chunk {chunk.index + 1} completato — "
            f"condensato: {condensed[:80]}..."
        )
        log(f"Chunk {chunk.index + 1}/{len(chunks)} completato — {chunk.label}")

        from engine.cooldown_manager import get_cooldown_manager

        get_cooldown_manager().after_chunk(stop_event)

    log(f"[INGEST] File completato: {file_path.name} ({len(analyses)} chunk analizzati)")

    return FileIngestResult(
        source_file=str(file_path),
        total_chunks=len(chunks),
        analyses=analyses,
        final_summary=analyses[-1].condensed if analyses else "",
    )


def _default_log_fn(msg: str) -> None:
    from engine.orchestrator import get_orchestrator_state

    get_orchestrator_state().emit_log(msg)


if __name__ == "__main__":
    import argparse
    import threading

    from core.ai_tasks import llm_complete

    parser = argparse.ArgumentParser(description="Test sliding window ingest (Task 4)")
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="file_dir esplicita (projects/.../01_INGEST/<stem>)",
    )
    parser.add_argument(
        "--project-ingest",
        type=Path,
        help="project_ingest_dir (01_INGEST); crea <stem>/ automaticamente",
    )
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--overlap", type=int, default=OVERLAP_CHARS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.out_dir and not args.project_ingest:
        parser.error("Serve --out-dir o --project-ingest")

    max_tok = args.max_tokens or resolve_chunk_max_tokens()
    stop = threading.Event()

    result = sliding_window_analyze(
        args.file.resolve(),
        file_dir=args.out_dir.resolve() if args.out_dir else None,
        project_ingest_dir=args.project_ingest.resolve() if args.project_ingest else None,
        llm_fn=llm_complete,
        stop_event=stop,
        log_fn=_default_log_fn,
        max_tokens_per_chunk=max_tok,
        overlap_chars=args.overlap,
        skip_llm=args.dry_run,
    )
    print(
        f"OK: {result.source_file} — {result.total_chunks} chunk, "
        f"{len(result.analyses)} analisi LLM"
    )
