"""
Loop iterativo gap analysis — fault-tolerant, chunking token-aware, state resume.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from config import (
    DEFAULT_SOURCE_ROOT,
    GAP_USE_ALLM_RAG,
    PIPELINE_CHUNK_COOLDOWN_S,
    PIPELINE_HARDWARE_PROFILE,
    PIPELINE_MAX_CONCURRENCY,
)
from converters import extract_plain
from core.ai_tasks import (
    GAP_INTEGRATE_SYSTEM_PROMPT,
    _format_session_block,
    append_to_report,
    discover_lm_studio_model,
    init_gap_analysis_session,
    llm_complete,
    consolidate_gap_report_for_handoff,
    perform_gap_analysis,
    write_gap_report,
)
from core.chunking import TextChunk, split_markdown_sections
from core.token_budget import resolve_chunk_max_tokens, resolve_chunk_min_section_tokens
from core.gap_allm import fetch_sot_rag_context, sync_sot_to_anythingllm
from core.paths import (
    build_compact_sot_index,
    default_sot_directories,
    ensure_session_dirs,
    gap_report_path,
    gap_report_path_for_raw,
    ingest_rel_key,
    iter_ingest_files,
    raw_ingest_dir,
    sot_tier_labels,
)
from settings import GAP_BATCH_SIZE, GAP_SOT_LAST_DOCS_ONLY
from core.preflight import LocalAIPreflightError
from core.progress import progress_bar
from core.session_state import PipelineSessionState
from core.token_budget import TokenLimits, raw_budget_to_chars, resolve_token_limits

logger = logging.getLogger(__name__)


@dataclass
class GapPipelineContext:
    """Sessione gap aperta una volta — riusata nel loop continuo (1 file/iterazione)."""

    repo_root: Path
    ingest: Path
    cumulative: Path
    st: PipelineSessionState
    sot_parts: list[tuple[str, str]]
    sot_refs: list[str]
    workspace_slug: str
    limits: TokenLimits
    chunk_max: int
    all_files: list[Path]
    all_keys: list[str]
    existing_cumulative: str
    integrate: bool
    append_only: bool


def load_sot_corpus(
    sot_dirs: list[Path],
    *,
    repo_root: Path,
) -> tuple[list[tuple[str, str]], list[str], list[tuple[str, Path]]]:
    from core.paths import discover_sot_files_with_tier

    ordered = discover_sot_files_with_tier(sot_dirs, repo_root=repo_root)
    if GAP_SOT_LAST_DOCS_ONLY:
        ordered = [(t, p) for t, p in ordered if t == 1]
    if not ordered:
        logger.warning("Nessun file SOT trovato — verifica --sot-path")
    parts: list[tuple[str, str]] = []
    sot_refs: list[str] = []
    files_for_allm: list[tuple[str, Path]] = []

    for tier, p in ordered:
        try:
            rel = p.relative_to(repo_root).as_posix()
        except ValueError:
            rel = p.name
        sot_refs.append(rel)
        try:
            if p.suffix.lower() in {".md", ".markdown", ".txt"}:
                body = p.read_text(encoding="utf-8", errors="replace")
            else:
                body = extract_plain(p)
        except OSError as e:
            logger.error("Lettura SOT fallita %s: %s", rel, e)
            continue
        header = f"### SOT Tier {tier}: {rel}\n\n"
        parts.append((rel, header + body))
        files_for_allm.append((rel, p))
        logger.info("SOT caricato [tier %d]: %s (%d char)", tier, rel, len(body))

    return parts, sot_refs, files_for_allm


def _read_raw_body(path: Path) -> str:
    if path.suffix.lower() in {".md", ".markdown", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace")
    return extract_plain(path)


def _merge_chunk_deltas(rel_key: str, deltas: list[tuple[str, str]]) -> str:
    labels = [lab for lab, _ in deltas]
    parts = [
        f"# Gap analysis — `{rel_key}`\n",
        f"_Analisi pipeline: **{len(deltas)}** parte/i "
        f"({', '.join(labels[:8])}{'…' if len(labels) > 8 else ''})._\n",
    ]
    for label, md in deltas:
        parts.append(f"\n## Chunk: {label}\n\n{md.strip()}\n")
    return "\n".join(parts)


def _process_single_file(
    *,
    raw_file: Path,
    rel_key: str,
    sot_parts: list[tuple[str, str]],
    sot_refs: list[str],
    workspace_slug: str,
    cumulative_path: Path,
    existing_cumulative: str,
    integrate: bool,
    append_only: bool,
    state: PipelineSessionState,
    raw_token_budget: int,
    sot_char_budget: int,
    repo_root: Path,
    start_chunk: int = 0,
    chunk_max_tokens: int | None = None,
) -> str:
    """Ritorna il cumulative report aggiornato."""
    from core.token_budget import count_tokens

    raw_body = _read_raw_body(raw_file)
    chunk_max = chunk_max_tokens or raw_token_budget
    chunks: list[TextChunk] = split_markdown_sections(
        raw_body,
        max_tokens=chunk_max,
        min_section_tokens=resolve_chunk_min_section_tokens(),
    )
    total_chunks = len(chunks)
    file_tokens = count_tokens(raw_body)
    print(
        f"  Chunking: {total_chunks} parti "
        f"(max {chunk_max} tok/chunk, file ~{file_tokens} tok)"
    )
    logger.info(
        "Piano chunk %s: %d parti, max %d tok/chunk, file ~%d tok",
        rel_key,
        total_chunks,
        chunk_max,
        file_tokens,
    )

    state.mark_processing(rel_key, chunk_index=start_chunk, chunks_total=total_chunks)

    rag_every = os.environ.get("GAP_RAG_PER_CHUNK", "").lower() in ("1", "true", "yes")
    rag_block_initial = ""

    if workspace_slug and not rag_every:
        rag_block_initial = fetch_sot_rag_context(
            workspace_slug=workspace_slug,
            raw_rel=rel_key,
            raw_excerpt=raw_body[:8000],
        )
        if rag_block_initial:
            logger.info(
                "RAG AnythingLLM: chunk 1/%d (GAP_RAG_PER_CHUNK=true → RAG su ogni parte)",
                total_chunks,
            )
        else:
            logger.warning("RAG AnythingLLM: nessun hit — solo indice SOT compatto")

    if rag_block_initial:
        # Con RAG: non reinserire l'indice SOT nel prompt (evita 11k+ token → context exceeded)
        sot_for_llm = [
            (
                "LAST DOCS",
                "_Contesto canonico via RAG AnythingLLM (sezione sotto). "
                "Non ripetere l'indice SOT inline._\n",
            )
        ]
        max_sot_chars = int(os.environ.get("GAP_MAX_SOT_CHARS_WITH_RAG", "600"))
        logger.info(
            "SOT nel prompt: solo RAG (max ~%d char SOT inline)",
            max_sot_chars,
        )
    else:
        sot_for_llm = sot_parts
        max_sot_chars = min(raw_budget_to_chars(sot_char_budget), raw_budget_to_chars(2500))
        logger.warning("RAG non disponibile — SOT inline troncata (solo LAST DOCS)")

    max_raw_chars = raw_budget_to_chars(raw_token_budget)
    deltas: list[tuple[str, str]] = []
    use_integrate = integrate and not append_only

    chunk_iter = chunks[start_chunk:]
    if not chunk_iter and start_chunk > 0:
        logger.warning(
            "Resume chunk %d/%d oltre la fine — reset analisi per %s",
            start_chunk,
            total_chunks,
            rel_key,
        )
        start_chunk = 0
        state.mark_processing(rel_key, chunk_index=0, chunks_total=total_chunks)
        chunk_iter = chunks

    if not chunk_iter:
        logger.warning("File vuoto o senza chunk analizzabili: %s", rel_key)
        merged_body = "_Nessun contenuto analizzabile nel file grezzo._"
        per_file = gap_report_path_for_raw(rel_key, repo_root)
        write_gap_report(
            str(per_file),
            merged_body,
            source_file=rel_key,
            sot_references=sot_refs,
        )
        if append_only or not use_integrate:
            append_to_report(
                str(cumulative_path),
                _format_session_block(rel_key, merged_body),
                source_file=rel_key,
                sot_references=sot_refs,
            )
            return cumulative_path.read_text(encoding="utf-8", errors="replace")
        return existing_cumulative

    chunk_bar = progress_bar(
        chunk_iter,
        desc="  chunks",
        total=len(chunk_iter),
        unit="chk",
        leave=False,
    )
    for chunk in chunk_bar:
        chunk_rel = f"{rel_key}#chunk-{chunk.index}"
        if hasattr(chunk_bar, "set_postfix_str"):
            chunk_bar.set_postfix_str(
                f"{chunk.index + 1}/{total_chunks} ~{chunk.token_estimate}tok",
                refresh=False,
            )
        logger.info(
            "[GAP] %s — chunk %d/%d (%s, ~%d token)",
            rel_key,
            chunk.index + 1,
            total_chunks,
            chunk.label,
            chunk.token_estimate,
        )

        prompt_label = (
            f"{rel_key} (sezione: {chunk.label}, chunk {chunk.index + 1}/{total_chunks})"
        )
        out_md, bundle, delta_md = perform_gap_analysis(
            sot_parts=sot_for_llm,
            raw_rel_path=prompt_label,
            raw_body=chunk.text,
            existing_report=existing_cumulative if use_integrate else "",
            max_sot_chars=max_sot_chars,
            max_raw_chars=max_raw_chars,
            integrate_report=False,
            chunk_index=chunk.index,
            chunks_total=total_chunks,
            chunk_label=chunk.label,
            rag_context=(
                fetch_sot_rag_context(
                    workspace_slug=workspace_slug,
                    raw_rel=rel_key,
                    raw_excerpt=chunk.text[:4000],
                )
                if rag_every and workspace_slug
                else (rag_block_initial if chunk.index == start_chunk else "")
            ),
            sot_references=sot_refs,
        )
        if bundle.warnings:
            logger.warning("Avvisi contesto: %s", "; ".join(bundle.warnings))
        deltas.append((chunk.label, delta_md))
        state.mark_chunk_done(rel_key, chunk.index, flush_every=5)
        if PIPELINE_CHUNK_COOLDOWN_S > 0 and chunk.index + 1 < total_chunks:
            time.sleep(PIPELINE_CHUNK_COOLDOWN_S)

    merged_body = _merge_chunk_deltas(rel_key, deltas)
    chunk_labels = [lab for lab, _ in deltas]

    consolidate = os.environ.get("GAP_REPORT_CONSOLIDATE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    if consolidate and total_chunks > 1:
        logger.info(
            "Consolidamento report handoff (%d chunk → report unico)", total_chunks
        )
        try:
            merged_body = consolidate_gap_report_for_handoff(
                rel_key=rel_key,
                merged_chunk_report=merged_body,
                sot_refs=sot_refs,
                chunks_total=total_chunks,
            )
        except Exception as e:
            logger.warning(
                "Consolidamento fallito (%s) — salvo versione per-chunk", e
            )

    sot_tiers = "1" if GAP_SOT_LAST_DOCS_ONLY else "1,2"
    write_per_file = os.environ.get("GAP_PER_FILE_REPORTS", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    if write_per_file:
        per_file = gap_report_path_for_raw(rel_key, repo_root)
        write_gap_report(
            str(per_file),
            merged_body,
            source_file=rel_key,
            sot_references=sot_refs,
            chunks_total=total_chunks,
            chunk_labels=chunk_labels,
            sot_tiers=sot_tiers,
        )
        logger.info("Report per-file: %s", per_file)
    else:
        logger.debug("Report per-file disabilitato — solo Gap_Report_Generale.md")

    if append_only or not use_integrate:
        from core.ai_tasks import _format_general_report_entry

        section = _format_general_report_entry(rel_key, merged_body)
        append_to_report(
            str(cumulative_path),
            section,
            source_file=rel_key,
            sot_references=sot_refs,
            sot_tiers=sot_tiers,
            is_general_report=True,
        )
        return cumulative_path.read_text(encoding="utf-8", errors="replace")

    user_integrate = f"""# Gap Report esistente

{existing_cumulative[: raw_budget_to_chars(2000)]}

---

# Nuove scoperte unite da chunk (`{rel_key}`)

{merged_body}

---

Produci il Gap Report COMPLETO aggiornato (Markdown)."""
    full_cumulative = llm_complete(
        system_prompt=GAP_INTEGRATE_SYSTEM_PROMPT,
        user_message=user_integrate,
        temperature=0.1,
        max_tokens=8192,
    )
    write_gap_report(
        str(cumulative_path),
        full_cumulative,
        source_file="Gap_Report_Generale.md",
        sot_references=sot_refs,
    )
    return full_cumulative


def open_gap_pipeline(
    *,
    repo_root: Path = DEFAULT_SOURCE_ROOT,
    ingest_root: Path | None = None,
    sot_paths: list[Path] | None = None,
    integrate: bool = False,
    append_only: bool = True,
    skip_allm: bool = False,
    force_allm_sync: bool = False,
    state: PipelineSessionState | None = None,
) -> GapPipelineContext | None:
    """Inizializza sessione gap (preflight, SOT, AnythingLLM). Ritorna None se server non pronti."""
    ensure_session_dirs(repo_root)
    ingest = ingest_root or raw_ingest_dir(repo_root)
    cumulative = gap_report_path(repo_root)
    st = state or PipelineSessionState()
    st.begin_pipeline("gap_analysis")

    sot_dirs = sot_paths or default_sot_directories(repo_root)
    require_allm = GAP_USE_ALLM_RAG and not skip_allm
    try:
        init_gap_analysis_session(require_allm=require_allm)
    except LocalAIPreflightError:
        return None

    model_id = discover_lm_studio_model()
    limits = resolve_token_limits(model_id)
    chunk_max = resolve_chunk_max_tokens(limits)
    logger.info(
        "Token budget: context=%d raw_input=%d chunk_max=%d sot~%d (modello=%s)",
        limits.context_tokens,
        limits.raw_input_budget,
        chunk_max,
        limits.sot_budget_tokens,
        limits.model_id,
    )
    prof = PIPELINE_HARDWARE_PROFILE or "custom"
    print(
        f"Profilo: {prof} | concorrenza LLM={PIPELINE_MAX_CONCURRENCY} "
        f"| batch file/run={GAP_BATCH_SIZE}"
    )
    print(
        f"Chunking gap: max {chunk_max} token per sezione "
        f"(GAP_CHUNK_MAX_TOKENS; file grandi → più chunk, non un solo «full»)"
    )

    if GAP_SOT_LAST_DOCS_ONLY:
        print("SOT confronto: SOLO `LAST DOCS/` (tier 1 canonico)")
    else:
        print("SOT confronto: tier 1 `LAST DOCS/` + tier 2 `Documentazione vecchia/`")
        print(
            "  AnythingLLM: includi anche documentazione vecchia nel workspace SOT "
            "(Salva e incorpora dall'UI)."
        )
    for label in sot_tier_labels(repo_root):
        logger.info("Gerarchia SOT: %s", label)

    sot_parts, sot_refs, sot_files = load_sot_corpus(sot_dirs, repo_root=repo_root)
    if not sot_parts:
        raise RuntimeError("Nessun documento SOT caricato.")

    workspace_slug = ""
    if require_allm:
        try:
            workspace_slug = sync_sot_to_anythingllm(sot_files, force=force_allm_sync)
        except Exception as e:
            logger.warning("Sync SOT AnythingLLM fallita: %s", e)

    all_files = list(iter_ingest_files(ingest))
    all_keys = [ingest_rel_key(p, ingest) for p in all_files]
    st.reconcile_with_ingest_keys(set(all_keys))
    st.register_all_files_pending(all_keys)

    existing_cumulative = ""
    if cumulative.is_file():
        existing_cumulative = cumulative.read_text(encoding="utf-8", errors="replace")

    return GapPipelineContext(
        repo_root=repo_root,
        ingest=ingest,
        cumulative=cumulative,
        st=st,
        sot_parts=sot_parts,
        sot_refs=sot_refs,
        workspace_slug=workspace_slug,
        limits=limits,
        chunk_max=chunk_max,
        all_files=all_files,
        all_keys=all_keys,
        existing_cumulative=existing_cumulative,
        integrate=integrate,
        append_only=append_only,
    )


def run_gap_file_batch(ctx: GapPipelineContext, *, limit: int | None = None) -> int:
    """Processa fino a `limit` file dalla coda (default GAP_BATCH_SIZE)."""
    st = ctx.st
    queue = st.pending_or_resume_files(ctx.all_keys)
    stats = st.stats()
    remaining = st.work_remaining(ctx.all_keys)
    print(
        f"\nGap queue: {remaining} in coda "
        f"(completati={stats['completed']}, totale={stats['total']})"
    )
    if st.interrupted_file():
        logger.info("Ripresa file interrotto: %s", st.interrupted_file())
    if not queue:
        print("Nessun file in coda.")
        return 0

    run_cap = limit if limit is not None else GAP_BATCH_SIZE
    batch_n = min(len(queue), run_cap)
    print(f"Questa iterazione: fino a {batch_n} file\n")

    processed = 0
    for key in queue[:batch_n]:
        print(f"\n--- Documento {processed + 1}/{batch_n}: {key} ---")
        raw_file = ctx.ingest / Path(key)
        if not raw_file.is_file():
            raw_file = next(
                (p for p in ctx.all_files if ingest_rel_key(p, ctx.ingest) == key),
                None,
            )
        if raw_file is None or not Path(raw_file).is_file():
            logger.error("File ingest non trovato: %s", key)
            st.mark_failed(key, "file not found")
            continue

        start_chunk = st.resume_chunk_index(key) if st.file_status(key) == "processing" else 0
        try:
            ctx.existing_cumulative = _process_single_file(
                raw_file=Path(raw_file),
                rel_key=key,
                sot_parts=ctx.sot_parts,
                sot_refs=ctx.sot_refs,
                workspace_slug=ctx.workspace_slug,
                cumulative_path=ctx.cumulative,
                existing_cumulative=ctx.existing_cumulative,
                integrate=ctx.integrate,
                append_only=ctx.append_only,
                state=st,
                raw_token_budget=ctx.limits.raw_input_budget,
                sot_char_budget=ctx.limits.sot_budget_tokens,
                repo_root=ctx.repo_root,
                start_chunk=start_chunk,
                chunk_max_tokens=ctx.chunk_max,
            )
            st.mark_completed(key)
            processed += 1
        except Exception as e:
            logger.exception("Gap fallito su %s: %s", key, e)
            st.mark_failed(key, str(e))

        if processed >= batch_n:
            break

    pruned = st.prune_completed(
        keep_last_n=int(os.environ.get("GAP_STATE_KEEP_COMPLETED", "200"))
    )
    if pruned:
        logger.info("State pruning: %d entry completed rimosse", pruned)

    logger.info("Gap batch: %d file processati -> %s", processed, ctx.cumulative)
    return processed


def run_resilient_gap_pipeline(
    *,
    repo_root: Path = DEFAULT_SOURCE_ROOT,
    ingest_root: Path | None = None,
    sot_paths: list[Path] | None = None,
    limit: int | None = None,
    integrate: bool = False,
    append_only: bool = True,
    skip_allm: bool = False,
    force_allm_sync: bool = False,
    state: PipelineSessionState | None = None,
    ctx: GapPipelineContext | None = None,
) -> int:
    """
    Pipeline gap fault-tolerant su 01_RAW_INGEST con resume da pipeline_state.json.
    """
    if ctx is None:
        opened = open_gap_pipeline(
            repo_root=repo_root,
            ingest_root=ingest_root,
            sot_paths=sot_paths,
            integrate=integrate,
            append_only=append_only,
            skip_allm=skip_allm,
            force_allm_sync=force_allm_sync,
            state=state,
        )
        if opened is None:
            return 0
        ctx = opened
    return run_gap_file_batch(ctx, limit=limit)


def run_continuous_gap_pipeline(
    *,
    repo_root: Path = DEFAULT_SOURCE_ROOT,
    ingest_root: Path | None = None,
    sot_paths: list[Path] | None = None,
    integrate: bool = False,
    append_only: bool = True,
    skip_allm: bool = False,
    force_allm_sync: bool = False,
    state: PipelineSessionState | None = None,
    files_per_iteration: int = 1,
    max_rounds: int | None = None,
) -> int:
    """
    Loop: 1 file (default) per iterazione fino a coda vuota o max_rounds.
    Preflight/SOT/AnythingLLM una sola volta all'inizio.
    """
    ctx = open_gap_pipeline(
        repo_root=repo_root,
        ingest_root=ingest_root,
        sot_paths=sot_paths,
        integrate=integrate,
        append_only=append_only,
        skip_allm=skip_allm,
        force_allm_sync=force_allm_sync,
        state=state,
    )
    if ctx is None:
        return 0

    per_iter = max(1, files_per_iteration)
    file_pause = float(os.environ.get("PIPELINE_FILE_PAUSE_S", "0"))
    total_done = 0
    round_n = 0
    stall_rounds = 0

    eco = "eco" in (PIPELINE_HARDWARE_PROFILE or "").lower()
    pause_hint = (
        f"pausa tra file {file_pause}s"
        if file_pause > 0
        else "nessuna pausa tra file"
    )
    if eco:
        print(
            f"\nModalita CONTINUA + ECO: {per_iter} file/iter, {pause_hint}, "
            f"cooldown LM {os.environ.get('PIPELINE_LM_COOLDOWN_S', '?')}s.\n"
            f"Carico GPU ridotto — adatta per lasciare girare ore/giorni.\n"
            f"(Ctrl+C per fermare — lo state riprende al prossimo avvio)\n"
        )
    else:
        print(
            f"\nModalita CONTINUA: {per_iter} file per iterazione, "
            f"prossimo file automatico fino a fine coda.\n"
            f"(Ctrl+C per fermare — lo state riprende al prossimo avvio)\n"
        )

    while True:
        round_n += 1
        if max_rounds is not None and round_n > max_rounds:
            print(f"Raggiunto max_rounds={max_rounds}. Stop.")
            break

        ctx.st.load()
        before = ctx.st.work_remaining(ctx.all_keys)
        if before == 0:
            print("\nCoda completata — nessun file pending/processing/failed.")
            break

        n = run_gap_file_batch(ctx, limit=per_iter)
        total_done += n
        ctx.st.load()
        stats = ctx.st.stats()
        after = ctx.st.work_remaining(ctx.all_keys)

        print(
            f"\n>>> Iterazione {round_n} | +{n} file | "
            f"Sessione totale: {total_done} | "
            f"In coda: {after} (ok={stats['completed']}, fail={stats['failed']})"
        )

        if n == 0:
            stall_rounds += 1
            if after == 0:
                break
            if stall_rounds >= 3:
                print(
                    f"Stop: nessun progresso dopo {stall_rounds} iterazioni "
                    f"({stats['failed']} failed in state)."
                )
                break
        else:
            stall_rounds = 0

        if after == 0:
            print("\nCoda completata.")
            break

        if file_pause > 0:
            time.sleep(file_pause)

    print(f"\nContinuo terminato: {total_done} file analizzati in questa sessione.")
    return total_done


def run_gap_analysis_loop(
    *,
    repo_root: Path,
    sot_paths: list[Path],
    target_path: Path,
    report_file: Path | None = None,
    integrate: bool = True,
    append_only: bool = False,
    limit: int | None = None,
    max_sot_chars: int | None = None,
    max_raw_chars: int | None = None,
    skip_allm: bool = False,
    force_allm_sync: bool = False,
) -> int:
    """Compatibilita CLI: delega alla pipeline resilient se target e sotto ingest."""
    ingest = raw_ingest_dir(repo_root)
    try:
        target_path.resolve().relative_to(ingest.resolve())
        under_ingest = True
    except ValueError:
        under_ingest = target_path.resolve() == ingest.resolve()

    if under_ingest or target_path.resolve() == ingest.resolve():
        return run_resilient_gap_pipeline(
            repo_root=repo_root,
            ingest_root=ingest,
            sot_paths=sot_paths,
            limit=limit,
            integrate=integrate,
            append_only=append_only,
            skip_allm=skip_allm,
            force_allm_sync=force_allm_sync,
        )

    # Path esterno singolo: loop legacy senza state file per ingest
    from core.paths import iter_target_files

    ensure_session_dirs(repo_root)
    cumulative = report_file or gap_report_path(repo_root)
    require_allm = GAP_USE_ALLM_RAG and not skip_allm
    try:
        init_gap_analysis_session(require_allm=require_allm)
    except LocalAIPreflightError:
        return 0

    limits = resolve_token_limits(discover_lm_studio_model())
    sot_parts, sot_refs, sot_files = load_sot_corpus(sot_paths, repo_root=repo_root)
    if not sot_parts:
        raise RuntimeError("Nessun documento SOT caricato.")

    workspace_slug = ""
    if require_allm:
        try:
            workspace_slug = sync_sot_to_anythingllm(sot_files, force=force_allm_sync)
        except Exception as e:
            logger.warning("Sync SOT: %s", e)

    existing_cumulative = ""
    if cumulative.is_file():
        existing_cumulative = cumulative.read_text(encoding="utf-8", errors="replace")

    st = PipelineSessionState()
    count = 0
    for raw_file in iter_target_files(target_path, repo_root=repo_root):
        try:
            rel = raw_file.relative_to(repo_root).as_posix()
        except ValueError:
            rel = raw_file.as_posix()
        key = rel
        if not st.should_process(key):
            continue
        try:
            existing_cumulative = _process_single_file(
                raw_file=raw_file,
                rel_key=key,
                sot_parts=sot_parts,
                sot_refs=sot_refs,
                workspace_slug=workspace_slug,
                cumulative_path=cumulative,
                existing_cumulative=existing_cumulative,
                integrate=integrate,
                append_only=append_only,
                state=st,
                raw_token_budget=limits.raw_input_budget,
                sot_char_budget=limits.sot_budget_tokens,
                repo_root=repo_root,
            )
            st.mark_completed(key)
            count += 1
        except Exception as e:
            st.mark_failed(key, str(e))
        if limit and count >= limit:
            break
    return count
