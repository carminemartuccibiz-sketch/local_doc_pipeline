"""
engine/ingest_processor.py — Sliding Window Context (Task 4 blueprint).

Processo per file:
  1. Lettura (extract_plain) + chunking strutturale con overlap sliding window
  2. Salvataggio in projects/<slug>/01_INGEST/<stem>/
  3. Loop analisi LLM con condensato del chunk precedente
  4. Append su analysis.md chunk per chunk
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.chunking import TextChunk, split_markdown_sections
from core.context_budget import truncate_middle
from core.converters import extract_plain
from core.file_io import atomic_write_json
from core.token_budget import count_tokens, raw_budget_to_chars, resolve_chunk_max_tokens

logger = logging.getLogger(__name__)

# ─── Limiti sicurezza (audit GPT §2.3 — anti OOM / contesto LM) ───────────────

INGEST_SAFETY_MARGIN = int(os.environ.get("INGEST_SAFETY_MARGIN", "128"))
INGEST_MAX_CHUNKS = int(os.environ.get("INGEST_MAX_CHUNKS", "250"))
INGEST_MAX_DOC_RATIO = float(os.environ.get("INGEST_MAX_DOC_RATIO", "0.55"))
INGEST_ANALYZE_MAX_OUTPUT = int(os.environ.get("INGEST_ANALYZE_MAX_OUTPUT", "800"))
INGEST_CONDENSE_MAX_OUTPUT = int(os.environ.get("INGEST_CONDENSE_MAX_OUTPUT", "200"))
_CONVERTER_ERROR_MARKERS = ("_Errore", "_non installato", "_Nessun testo")


class IngestReadError(ValueError):
    """Lettura/conversione file fallita — non far crashare il worker senza messaggio."""


class IngestBudgetError(ValueError):
    """Documento o prompt troppo grande per il contesto LM — blocco pre-chiamata LLM."""

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


@dataclass(slots=True)
class IngestCallBudget:
    """Budget dinamico per preflight prima di ogni chiamata LLM."""

    model_hint: str
    context_tokens: int
    chunk_max_tokens: int
    output_reserve: int
    max_document_tokens: int
    max_chunks: int


# ─── Chunking strutturale + overlap (audit GPT §2.1 / Perplexity §3.1) ─────────

OVERLAP_CHARS = 400  # ~100 token di sovrapposizione tra chunk adiacenti
SLIDING_CONTEXT_CHUNKS = 2  # audit GPT §2.2 — rolling, non solo ultimo condensato
_OVERLAP_PREFIX = "[...contesto dal blocco precedente...]\n"

_FENCE_RE = re.compile(r"^```[^\n]*$", re.MULTILINE)
_PARA_BREAK_RE = re.compile(r"\n\s*\n+")
_LINE_BREAK_RE = re.compile(r"\n")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÿ0-9\"'`(])")


@dataclass(frozen=True, slots=True)
class _FenceSpan:
    start: int
    end: int


def _fence_spans(text: str) -> list[_FenceSpan]:
    """Intervalli ``` ... ``` (linee fence su righe dedicate)."""
    lines = text.splitlines(keepends=True)
    spans: list[_FenceSpan] = []
    offset = 0
    open_start: int | None = None
    for line in lines:
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            if open_start is None:
                open_start = offset
            else:
                spans.append(_FenceSpan(open_start, offset + len(line)))
                open_start = None
        offset += len(line)
    return spans


def _position_in_fence(pos: int, spans: list[_FenceSpan]) -> bool:
    return any(s.start <= pos < s.end for s in spans)


def _snap_out_of_fence(pos: int, spans: list[_FenceSpan], *, forward: bool) -> int:
    """Allinea pos fuori da un blocco fenced (non tagliare snippet di codice)."""
    for span in spans:
        if span.start < pos < span.end:
            return span.end if forward else span.start
    return pos


def _collect_boundary_candidates(text: str, upto: int, spans: list[_FenceSpan]) -> list[int]:
    """Punti di taglio sicuri in text[:upto] (esclusi interni fence)."""
    upto = max(0, min(upto, len(text)))
    if upto == 0:
        return [0]
    candidates: set[int] = {0, upto}
    for m in _PARA_BREAK_RE.finditer(text, 0, upto):
        candidates.add(m.end())
    for m in _LINE_BREAK_RE.finditer(text, 0, upto):
        if m.start() > 0:
            candidates.add(m.start())
    for m in _SENTENCE_BREAK_RE.finditer(text, 0, upto):
        candidates.add(m.end())
    for span in spans:
        if span.start <= upto:
            candidates.add(span.start)
        if span.end <= upto:
            candidates.add(span.end)
    safe = [p for p in candidates if not _position_in_fence(p, spans)]
    return sorted(set(safe))


def _find_safe_start(text: str, rough_start: int, *, min_start: int = 0) -> int:
    """
    Allinea rough_start a un confine strutturale (paragrafo > riga > frase).
    Non inizia a metà di un blocco ```.
    """
    spans = _fence_spans(text)
    rough_start = max(min_start, min(rough_start, len(text)))
    candidates = _collect_boundary_candidates(text, len(text), spans)
    viable = [p for p in candidates if min_start <= p <= rough_start]
    if not viable:
        return _snap_out_of_fence(rough_start, spans, forward=True)
    return viable[-1]


def _normalize_overlap_segment(segment: str) -> str:
    """Ripulisce overlap: niente fence aperti, niente righe spezzate."""
    segment = segment.strip("\n")
    if not segment:
        return ""
    spans = _fence_spans(segment)
    if segment.count("```") % 2 == 1:
        first = segment.find("```")
        last = segment.rfind("```")
        if first >= 0 and last > first:
            segment = segment[first : last + 3]
        elif first >= 0:
            segment = segment[first:].lstrip("\n")
            if segment.startswith("```"):
                nl = segment.find("\n")
                if nl >= 0:
                    segment = segment[nl + 1 :]
    if spans and _position_in_fence(0, spans):
        segment = segment[_snap_out_of_fence(0, spans, forward=True) :]
    return segment.strip()


def _extract_structural_overlap(prev_text: str, max_chars: int) -> str:
    """
    Overlap dalla coda del chunk precedente, tagliato su confini sicuri
    (paragrafo / frase / fence), non su numero fisso di caratteri grezzi.
    """
    if max_chars <= 0 or not prev_text.strip():
        return ""
    if len(prev_text) <= max_chars:
        return _normalize_overlap_segment(prev_text)

    rough = len(prev_text) - max_chars
    min_start = max(0, len(prev_text) - max_chars * 2)
    safe_start = _find_safe_start(prev_text, rough, min_start=min_start)
    return _normalize_overlap_segment(prev_text[safe_start:])


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in _PARA_BREAK_RE.split(text) if p.strip()]
    return parts if parts else [text.strip()]


def _pack_units_into_chunks(
    units: list[str],
    *,
    max_tokens: int,
    base_label: str,
) -> list[TextChunk]:
    """Accumula paragrafi (o frasi) fino al budget token."""
    chunks: list[TextChunk] = []
    buf: list[str] = []
    buf_tokens = 0
    part = 0

    def flush() -> None:
        nonlocal part, buf, buf_tokens
        if not buf:
            return
        joined = "\n\n".join(buf)
        label = base_label if part == 0 else f"{base_label} (part {part})"
        chunks.append(
            TextChunk(len(chunks), label[:80], joined, count_tokens(joined))
        )
        part += 1
        buf = []
        buf_tokens = 0

    for unit in units:
        unit_tokens = count_tokens(unit)
        if unit_tokens > max_tokens:
            flush()
            chunks.extend(
                _split_oversized_unit_by_sentences(unit, max_tokens=max_tokens, base_label=base_label)
            )
            continue
        if buf_tokens + unit_tokens > max_tokens and buf:
            flush()
        buf.append(unit)
        buf_tokens += unit_tokens
    flush()
    return chunks


def _split_oversized_unit_by_sentences(
    text: str,
    *,
    max_tokens: int,
    base_label: str,
) -> list[TextChunk]:
    """Spezza testo lungo per frasi, mai per slice a caratteri fissi."""
    sentences = [s.strip() for s in _SENTENCE_BREAK_RE.split(text) if s.strip()]
    if not sentences:
        spans = _fence_spans(text)
        if spans:
            out: list[TextChunk] = []
            pos = 0
            for i, span in enumerate(spans):
                if span.start > pos:
                    mid = text[pos:span.start].strip()
                    if mid:
                        out.extend(
                            _pack_units_into_chunks([mid], max_tokens=max_tokens, base_label=base_label)
                        )
                block = text[span.start : span.end].strip()
                if block:
                    out.append(
                        TextChunk(len(out), f"{base_label} (code {i + 1})", block, count_tokens(block))
                    )
                pos = span.end
            if pos < len(text):
                tail = text[pos:].strip()
                if tail:
                    out.extend(
                        _pack_units_into_chunks([tail], max_tokens=max_tokens, base_label=base_label)
                    )
            return out if out else [TextChunk(0, base_label, text, count_tokens(text))]
        return [TextChunk(0, f"{base_label} (full)", text, count_tokens(text))]

    return _pack_units_into_chunks(sentences, max_tokens=max_tokens, base_label=base_label)


def _split_text_at_safe_boundaries(
    text: str,
    *,
    max_tokens: int,
    base_label: str,
) -> list[TextChunk]:
    """Suddivisione paragrafo → frasi; rispetta blocchi ```."""
    if count_tokens(text) <= max_tokens:
        return [TextChunk(0, base_label[:80], text, count_tokens(text))]
    return _pack_units_into_chunks(
        _split_paragraphs(text),
        max_tokens=max_tokens,
        base_label=base_label,
    )


def _refine_chunks_to_token_budget(
    chunks: list[TextChunk],
    *,
    max_tokens: int,
) -> list[TextChunk]:
    """Ri-splitta chunk troppo grandi usciti da split_markdown_sections."""
    refined: list[TextChunk] = []
    for chunk in chunks:
        if count_tokens(chunk.text) <= max_tokens:
            refined.append(chunk)
        else:
            refined.extend(
                _split_text_at_safe_boundaries(
                    chunk.text,
                    max_tokens=max_tokens,
                    base_label=chunk.label,
                )
            )
    for i, c in enumerate(refined):
        c.index = i
    return refined

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


def _ingest_use_chunking_v2() -> bool:
    """Opt-in: semantic heading-tree chunker (core/chunking_v2.py)."""
    return os.environ.get("INGEST_USE_CHUNKING_V2", "").lower() in (
        "1",
        "true",
        "yes",
    )


def ingest_chunk_strategy() -> str:
    if _ingest_use_chunking_v2():
        return "semantic_v2_heading_context"
    return "markdown_structural_overlap"


def _strip_v2_context_prefix(text: str) -> tuple[str, int]:
    """Rimuove prefisso overlap heading-context per localizzazione char nel body."""
    if not text.startswith("[Contesto:"):
        return text, 0
    close = text.find("]\n")
    if close < 0:
        return text, 0
    return text[close + 2 :], close + 2


def _resolve_ingest_model_hint() -> str:
    try:
        from core.ai_tasks import get_session_lm_model

        return get_session_lm_model()
    except Exception:
        return "cl100k_base"


def _semantic_chunks_to_text_chunks(
    body: str,
    semantic_chunks: list,
) -> tuple[list[TextChunk], list[ChunkMeta]]:
    """Mappa SemanticChunk → TextChunk + ChunkMeta per pipeline ingest / ai_tasks."""
    text_chunks: list[TextChunk] = []
    metas: list[ChunkMeta] = []
    search_at = 0

    for sc in semantic_chunks:
        raw_text, prefix_len = _strip_v2_context_prefix(sc.text)
        char_start = _locate_chunk_in_body(body, raw_text, search_at)
        char_end = min(len(body), char_start + len(raw_text))
        search_at = max(search_at, char_end)

        label = (sc.parent_heading or f"chunk_{sc.index + 1}")[:80]
        tok = count_tokens(sc.text, model_hint=_resolve_ingest_model_hint())
        text_chunks.append(
            TextChunk(
                index=sc.index,
                label=label,
                text=sc.text,
                token_estimate=tok,
            )
        )
        metas.append(
            ChunkMeta(
                index=sc.index,
                label=label,
                char_start=char_start,
                char_end=char_end,
                token_estimate=tok,
                overlap_with_prev_chars=prefix_len if sc.index > 0 else 0,
                overlap_with_next_chars=0,
            )
        )

    return text_chunks, metas


def _build_chunks_semantic_v2(
    body: str,
    *,
    max_tokens: int,
    model_hint: str | None = None,
) -> tuple[list[TextChunk], list[ChunkMeta]]:
    from core.chunking_v2 import semantic_chunk

    hint = model_hint or _resolve_ingest_model_hint()
    semantic = semantic_chunk(body, max_tokens=max_tokens, model_hint=hint)

    if not semantic:
        tok = count_tokens(body, model_hint=hint)
        return (
            [TextChunk(0, "full", body, tok)],
            [ChunkMeta(0, "full", 0, len(body), tok)],
        )

    try:
        from core.semantic_dedup import minhash_dedup

        semantic = minhash_dedup(semantic)
    except ImportError:
        pass

    return _semantic_chunks_to_text_chunks(body, semantic)


def _build_chunks_with_overlap(
    body: str,
    *,
    max_tokens: int,
    overlap_chars: int = OVERLAP_CHARS,
) -> tuple[list[TextChunk], list[ChunkMeta]]:
    """
    Chunking: sezioni Markdown (##) → paragrafi/frasi → overlap strutturale,
    oppure semantic_v2 (heading tree + overlap heading-context) se INGEST_USE_CHUNKING_V2=true.
    """
    if _ingest_use_chunking_v2():
        logger.info(
            "Ingest chunking: semantic_v2 (max_tokens=%d, model_hint=%s)",
            max_tokens,
            _resolve_ingest_model_hint(),
        )
        return _build_chunks_semantic_v2(body, max_tokens=max_tokens)

    base_chunks = split_markdown_sections(body, max_tokens=max_tokens)
    base_chunks = _refine_chunks_to_token_budget(base_chunks, max_tokens=max_tokens)

    if not base_chunks:
        base_chunks = _split_text_at_safe_boundaries(
            body, max_tokens=max_tokens, base_label="full"
        )

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
            tail = _extract_structural_overlap(prev_text, overlap_chars)
            if tail:
                overlap_prefix = f"{_OVERLAP_PREFIX}{tail}\n\n"
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


def _extract_condensed(
    analysis_text: str,
    *,
    llm_fn: Callable[..., str],
    user_message_override: str | None = None,
) -> str:
    """Estrae il condensato dall'analisi. Prima parsing ## CONDENSATO, poi fallback LLM."""
    if "## CONDENSATO" in analysis_text:
        parts = analysis_text.split("## CONDENSATO", 1)
        candidate = parts[1].strip().split("##")[0].strip()
        if len(candidate) > 30:
            return candidate

    user_msg = user_message_override if user_message_override is not None else analysis_text[:3000]
    return llm_fn(
        system_prompt=CONDENSE_SYSTEM_PROMPT,
        user_message=user_msg,
        temperature=0.05,
        max_tokens=INGEST_CONDENSE_MAX_OUTPUT,
    )


def _log_ui(log_fn: Callable[[str], None] | None, msg: str, *, level: str = "INFO") -> None:
    """Log verso logger + UI (prefisso ERROR/WARN per emit_log)."""
    if level == "ERROR":
        logger.error(msg)
        ui = f"[INGEST][ERROR] {msg.removeprefix('[INGEST] ')}"
    elif level == "WARN":
        logger.warning(msg)
        ui = f"[INGEST][WARN] {msg.removeprefix('[INGEST] ')}"
    else:
        logger.info(msg)
        ui = msg
    if log_fn:
        log_fn(ui)


def read_document_safe(file_path: Path, log_fn: Callable[[str], None] | None = None) -> str:
    """
    Lettura resiliente: dimensione max, encoding, file corrotti.
    Errori chiari verso UI (IngestReadError) senza crash opaco del worker.
    """
    from config.runtime import MAX_FILE_BYTES

    path = Path(file_path)
    if not path.exists():
        raise IngestReadError(f"File non trovato: {path}")
    if not path.is_file():
        raise IngestReadError(f"Percorso non è un file: {path}")

    try:
        size = path.stat().st_size
    except OSError as e:
        raise IngestReadError(f"Impossibile leggere metadati di {path.name}: {e}") from e

    if size == 0:
        raise IngestReadError(f"File vuoto: {path.name}")

    if size > MAX_FILE_BYTES:
        mib = size / (1024 * 1024)
        cap_mib = MAX_FILE_BYTES / (1024 * 1024)
        raise IngestReadError(
            f"{path.name} troppo grande ({mib:.1f} MiB, max {cap_mib:.1f} MiB). "
            "Suddividi il documento o alza PIPELINE_MAX_FILE_BYTES."
        )

    _log_ui(
        log_fn,
        f"[INGEST] Lettura: {path.name} ({size // 1024} KiB)",
    )

    try:
        body = extract_plain(path)
    except UnicodeDecodeError as e:
        raise IngestReadError(
            f"Encoding non UTF-8/CP1252 per {path.name} — converti in UTF-8 o .md"
        ) from e
    except PermissionError as e:
        raise IngestReadError(f"Permesso negato su {path.name}") from e
    except OSError as e:
        raise IngestReadError(f"I/O fallita su {path.name}: {e}") from e
    except MemoryError as e:
        raise IngestReadError(
            f"Memoria insufficiente leggendo {path.name} — file troppo grande per RAM"
        ) from e
    except Exception as e:
        raise IngestReadError(
            f"Estrazione testo fallita per {path.name}: {type(e).__name__}: {e}"
        ) from e

    stripped = body.strip()
    if not stripped:
        raise IngestReadError(f"Nessun testo estratto da {path.name}")

    if any(stripped.startswith(m) or m in stripped[:120] for m in _CONVERTER_ERROR_MARKERS):
        preview = stripped.replace("\n", " ")[:180]
        raise IngestReadError(
            f"Conversione non riuscita per {path.name}: {preview}"
        )

    return body


def _resolve_ingest_call_budget(chunk_token_budget: int) -> IngestCallBudget:
    """Budget dinamico dal modello LM attivo (GPT §2.3)."""
    model_hint = "cl100k_base"
    context = int(os.environ.get("GAP_MODEL_CONTEXT_TOKENS", "8192"))
    output_reserve = INGEST_ANALYZE_MAX_OUTPUT

    try:
        from core.ai_tasks import get_session_lm_model
        from core.token_budget import resolve_token_limits

        model_hint = get_session_lm_model()
        limits = resolve_token_limits(model_hint)
        context = limits.context_tokens
        output_reserve = min(INGEST_ANALYZE_MAX_OUTPUT, limits.response_reserve)
    except Exception as e:
        logger.debug("Budget ingest: fallback env (%s)", e)

    max_doc = max(512, int(context * INGEST_MAX_DOC_RATIO))
    return IngestCallBudget(
        model_hint=model_hint,
        context_tokens=context,
        chunk_max_tokens=chunk_token_budget,
        output_reserve=output_reserve,
        max_document_tokens=max_doc,
        max_chunks=INGEST_MAX_CHUNKS,
    )


def _preflight_whole_document(
    body: str,
    file_path: Path,
    budget: IngestCallBudget,
    log_fn: Callable[[str], None] | None,
) -> int:
    """Check pre-chunking: documento non deve saturare contesto / numero chunk."""
    total = count_tokens(body, model_hint=budget.model_hint)
    if total > budget.max_document_tokens:
        raise IngestBudgetError(
            f"{file_path.name}: ~{total} token totali (max {budget.max_document_tokens}). "
            "Suddividi il file o usa un modello con contesto maggiore."
        )

    est_chunks = max(1, (total + budget.chunk_max_tokens - 1) // budget.chunk_max_tokens)
    if est_chunks > budget.max_chunks:
        raise IngestBudgetError(
            f"{file_path.name}: ~{est_chunks} chunk stimati (max {budget.max_chunks}). "
            "Riduci il file o aumenta GAP_CHUNK_MAX_TOKENS nel .env."
        )

    _log_ui(
        log_fn,
        f"[INGEST] Preflight OK: ~{total} tok, ~{est_chunks} chunk (contesto {budget.context_tokens})",
    )
    return total


def _preflight_llm_payload(
    *,
    system_prompt: str,
    user_message: str,
    budget: IngestCallBudget,
    log_fn: Callable[[str], None] | None,
    label: str,
    max_output_tokens: int,
) -> str:
    """Check pre-chiamata LLM: tronca il prompt se necessario, blocca se ancora OOM."""
    sys_tok = count_tokens(system_prompt, model_hint=budget.model_hint)
    usr_tok = count_tokens(user_message, model_hint=budget.model_hint)
    max_input = (
        budget.context_tokens
        - sys_tok
        - max_output_tokens
        - INGEST_SAFETY_MARGIN
    )

    if max_input < 256:
        raise IngestBudgetError(
            f"{label}: budget input insufficiente ({max_input} tok liberi su "
            f"{budget.context_tokens} contesto)"
        )

    if usr_tok > max_input:
        _log_ui(
            log_fn,
            f"[INGEST] {label}: prompt ~{usr_tok} tok > {max_input}, troncamento sicuro",
            level="WARN",
        )
        user_message, _ = truncate_middle(
            user_message,
            raw_budget_to_chars(max_input),
            label,
        )
        usr_tok = count_tokens(user_message, model_hint=budget.model_hint)

    if sys_tok + usr_tok + max_output_tokens + INGEST_SAFETY_MARGIN > budget.context_tokens:
        raise IngestBudgetError(
            f"{label}: ancora troppo grande dopo troncamento (~{usr_tok} tok input)"
        )

    return user_message


def _resolve_ingest_chunk_tokens(requested: int) -> int:
    """Budget chunk dinamico da modello attivo (audit GPT §2.3), fallback a requested."""
    if requested != 1200:
        return requested
    try:
        from core.ai_tasks import get_session_lm_model
        from core.token_budget import resolve_chunk_max_tokens, resolve_token_limits

        limits = resolve_token_limits(get_session_lm_model())
        return resolve_chunk_max_tokens(limits)
    except Exception as e:
        logger.debug("Chunk budget dinamico non disponibile: %s", e)
        return requested


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

    chunk_token_budget = _resolve_ingest_chunk_tokens(max_tokens_per_chunk)
    call_budget = _resolve_ingest_call_budget(chunk_token_budget)

    # ── 1. Lettura resiliente + preflight documento ─────────────────────────
    try:
        body = read_document_safe(file_path, log_fn)
        _preflight_whole_document(body, file_path, call_budget, log_fn)
    except (IngestReadError, IngestBudgetError) as e:
        _log_ui(log_fn, str(e), level="ERROR")
        raise
    except Exception as e:
        _log_ui(
            log_fn,
            f"Lettura preflight fallita per {file_path.name}: {e}",
            level="ERROR",
        )
        raise IngestReadError(str(e)) from e

    try:
        chunks, metas = _build_chunks_with_overlap(
            body,
            max_tokens=chunk_token_budget,
            overlap_chars=overlap_chars,
        )
    except Exception as e:
        _log_ui(log_fn, f"Chunking fallito per {file_path.name}: {e}", level="ERROR")
        raise IngestReadError(f"Chunking fallito: {e}") from e

    if len(chunks) > call_budget.max_chunks:
        raise IngestBudgetError(
            f"{file_path.name}: {len(chunks)} chunk (max {call_budget.max_chunks})"
        )

    _log_ui(
        log_fn,
        f"[INGEST] {len(chunks)} chunk "
        f"(budget {chunk_token_budget} tok, strategy {ingest_chunk_strategy()})",
    )

    # ── 2. Cartella isolata per questo file ──────────────────────────────
    try:
        resolved_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, resolved_dir / f"original{file_path.suffix}")
    except OSError as e:
        _log_ui(log_fn, f"Scrittura cartella ingest fallita: {e}", level="ERROR")
        raise IngestReadError(str(e)) from e

    for chunk in chunks:
        chunk_path = resolved_dir / f"chunk_{chunk.index + 1:03d}.txt"
        try:
            chunk_path.write_text(chunk.text, encoding="utf-8", newline="\n")
        except OSError as e:
            _log_ui(log_fn, f"Scrittura {chunk_path.name} fallita: {e}", level="ERROR")
            raise IngestReadError(str(e)) from e

    source_md5 = hashlib.md5(body.encode("utf-8", errors="replace")).hexdigest()
    chunks_json = {
        "source_file": file_path.name,
        "source_md5": source_md5,
        "chunked_at": _format_chunked_at(),
        "chunk_strategy": ingest_chunk_strategy(),
        "chunk_token_budget": chunk_token_budget,
        "overlap_tokens": _overlap_tokens_estimate(
            overlap_chars, sample=body[:overlap_chars]
        ),
        "overlap_chars": overlap_chars if not _ingest_use_chunking_v2() else 0,
        "total_chunks": len(chunks),
        "chunks": [_chunk_meta_to_json(m, total=len(metas)) for m in metas],
    }
    try:
        atomic_write_json(resolved_dir / "chunks.json", chunks_json)
    except OSError as e:
        _log_ui(log_fn, f"chunks.json non scritto: {e}", level="ERROR")
        raise IngestReadError(str(e)) from e
    _log_ui(log_fn, f"[INGEST] Struttura salvata: {resolved_dir}")

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
    condensed_history: list[str] = []

    analyze_out = min(INGEST_ANALYZE_MAX_OUTPUT, call_budget.output_reserve)

    for chunk in chunks:
        if stop_event.is_set():
            _log_ui(log_fn, f"[STOP] Interrotto a chunk {chunk.index + 1}/{len(chunks)}")
            raise InterruptedError("Sliding window interrotto da Kill Switch")

        _log_ui(log_fn, f"[ANALYZE] Chunk {chunk.index + 1}/{len(chunks)}: {chunk.label}")

        context_block = ""
        if condensed_history:
            recent = condensed_history[-SLIDING_CONTEXT_CHUNKS:]
            parts = [
                f"### Blocco precedente -{len(recent) - i}\n{c}"
                for i, c in enumerate(recent)
            ]
            context_block = (
                "\n---\n**Contesto rolling (ultimi blocchi analizzati):**\n"
                + "\n".join(parts)
                + "\n---\n\n"
            )

        user_msg = (
            f"# Documento: `{file_path.name}`\n"
            f"# Frammento {chunk.index + 1} di {len(chunks)}: `{chunk.label}`\n"
            f"{context_block}"
            f"## Testo del frammento\n\n{chunk.text}"
        )

        label = f"chunk {chunk.index + 1}/{len(chunks)}"
        try:
            user_msg = _preflight_llm_payload(
                system_prompt=ANALYZE_SYSTEM_PROMPT,
                user_message=user_msg,
                budget=call_budget,
                log_fn=log_fn,
                label=label,
                max_output_tokens=analyze_out,
            )
        except IngestBudgetError as e:
            _log_ui(log_fn, str(e), level="ERROR")
            raise

        try:
            raw_analysis = llm_fn(
                system_prompt=ANALYZE_SYSTEM_PROMPT,
                user_message=user_msg,
                temperature=0.07,
                max_tokens=analyze_out,
            )
        except InterruptedError:
            raise
        except IngestBudgetError:
            raise
        except Exception as e:
            err = f"Analisi {label} fallita: {e}"
            _log_ui(log_fn, err, level="ERROR")
            if "timeout" in str(e).lower() or "OOM" in str(e).upper():
                raise IngestBudgetError(err) from e
            raise IngestReadError(err) from e

        try:
            condense_msg = _preflight_llm_payload(
                system_prompt=CONDENSE_SYSTEM_PROMPT,
                user_message=raw_analysis[:3000],
                budget=call_budget,
                log_fn=log_fn,
                label=f"condensato {label}",
                max_output_tokens=INGEST_CONDENSE_MAX_OUTPUT,
            )
            condensed = _extract_condensed(
                raw_analysis,
                llm_fn=llm_fn,
                user_message_override=condense_msg,
            )
        except IngestBudgetError:
            condensed = raw_analysis[:400].strip() or "(condensato non disponibile)"
        except Exception as e:
            logger.warning("Condensato chunk %s fallback: %s", chunk.index, e)
            _log_ui(
                log_fn,
                f"Condensato chunk {chunk.index + 1} fallback: {e}",
                level="WARN",
            )
            condensed = raw_analysis[:400].strip() or "(condensato non disponibile)"
        condensed_history.append(condensed)
        if len(condensed_history) > SLIDING_CONTEXT_CHUNKS + 1:
            condensed_history.pop(0)

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

        _log_ui(
            log_fn,
            f"[ANALYZE] Chunk {chunk.index + 1} completato — "
            f"condensato: {condensed[:80]}...",
        )

        from engine.cooldown_manager import get_cooldown_manager

        get_cooldown_manager().after_chunk(stop_event)

    _log_ui(
        log_fn,
        f"[INGEST] File completato: {file_path.name} ({len(analyses)} chunk analizzati)",
    )

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
