"""
Loop iterativo gap analysis — fault-tolerant, chunking token-aware, state resume.
"""
from __future__ import annotations

import logging
from pathlib import Path

from config import DEFAULT_SOURCE_ROOT, GAP_USE_ALLM_RAG
from converters import extract_plain
from core.ai_tasks import (
    GAP_INTEGRATE_SYSTEM_PROMPT,
    _format_session_block,
    append_to_report,
    discover_lm_studio_model,
    init_gap_analysis_session,
    llm_complete,
    perform_gap_analysis,
    write_gap_report,
)
from core.chunking import TextChunk, split_markdown_sections
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
from settings import GAP_SOT_LAST_DOCS_ONLY
from core.preflight import LocalAIPreflightError
from core.progress import progress_bar
from core.session_state import PipelineSessionState
from core.token_budget import raw_budget_to_chars, resolve_token_limits

logger = logging.getLogger(__name__)


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
    if len(deltas) == 1:
        return deltas[0][1]
    parts = [f"# Gap analysis — `{rel_key}`\n"]
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
) -> str:
    """Ritorna il cumulative report aggiornato."""
    raw_body = _read_raw_body(raw_file)
    chunks: list[TextChunk] = split_markdown_sections(
        raw_body,
        max_tokens=raw_token_budget,
    )
    total_chunks = len(chunks)

    state.mark_processing(rel_key, chunk_index=start_chunk, chunks_total=total_chunks)

    rag_block = ""
    if workspace_slug:
        rag_block = fetch_sot_rag_context(
            workspace_slug=workspace_slug,
            raw_rel=rel_key,
            raw_excerpt=raw_body[:8000],
        )

    if rag_block:
        sot_for_llm = build_compact_sot_index(sot_parts)
        max_sot_chars = raw_budget_to_chars(1200)
        logger.info("SOT nel prompt: indice compatto + RAG LAST DOCS")
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
            rag_context=rag_block if chunk.index == start_chunk else "",
            sot_references=sot_refs,
        )
        if bundle.warnings:
            logger.warning("Avvisi contesto: %s", "; ".join(bundle.warnings))
        deltas.append((chunk.label, delta_md))
        state.mark_chunk_done(rel_key, chunk.index, flush_every=5)

    merged_body = _merge_chunk_deltas(rel_key, deltas)
    per_file = gap_report_path_for_raw(rel_key, repo_root)
    write_gap_report(
        str(per_file),
        merged_body,
        source_file=rel_key,
        sot_references=sot_refs,
    )
    logger.info("Report SPEC: %s", per_file)

    if append_only or not use_integrate:
        section = _format_session_block(rel_key, merged_body)
        append_to_report(
            str(cumulative_path),
            section,
            source_file=rel_key,
            sot_references=sot_refs,
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
) -> int:
    """
    Pipeline gap fault-tolerant su 01_RAW_INGEST con resume da pipeline_state.json.
    """
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
        return 0

    model_id = discover_lm_studio_model()
    limits = resolve_token_limits(model_id)
    logger.info(
        "Token budget: context=%d raw_input=%d sot~%d (modello=%s)",
        limits.context_tokens,
        limits.raw_input_budget,
        limits.sot_budget_tokens,
        limits.model_id,
    )

    if GAP_SOT_LAST_DOCS_ONLY:
        print("SOT confronto: SOLO `LAST DOCS/` (tier 1 canonico)")
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

    queue = st.pending_or_resume_files(all_keys)
    stats = st.stats()
    print(
        f"\nGap queue: {len(queue)} da processare "
        f"(completati={stats['completed']}, totale={stats['total']})"
    )
    if st.interrupted_file():
        logger.info("Ripresa file interrotto: %s", st.interrupted_file())

    existing_cumulative = ""
    if cumulative.is_file():
        existing_cumulative = cumulative.read_text(encoding="utf-8", errors="replace")

    run_cap = limit if limit is not None else len(queue)
    batch_n = min(len(queue), run_cap)
    print(f"Modalita: 1 documento alla volta (max {batch_n} in questa esecuzione)\n")

    processed = 0
    file_bar = progress_bar(queue, desc="Gap Analysis", unit="file", total=batch_n)
    for key in file_bar:
        print(f"\n--- Documento {processed + 1}/{batch_n}: {key} ---")
        if hasattr(file_bar, "set_postfix_str"):
            file_bar.set_postfix_str(Path(key).name, refresh=True)
        raw_file = ingest / Path(key)
        if not raw_file.is_file():
            raw_file = next((p for p in all_files if ingest_rel_key(p, ingest) == key), None)
        if raw_file is None or not Path(raw_file).is_file():
            logger.error("File ingest non trovato: %s", key)
            st.mark_failed(key, "file not found")
            continue

        start_chunk = st.resume_chunk_index(key) if st.file_status(key) == "processing" else 0
        try:
            existing_cumulative = _process_single_file(
                raw_file=Path(raw_file),
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
                start_chunk=start_chunk,
            )
            st.mark_completed(key)
            processed += 1
        except Exception as e:
            logger.exception("Gap fallito su %s: %s", key, e)
            st.mark_failed(key, str(e))

        if limit and processed >= limit:
            break

    logger.info("Gap pipeline: %d file processati -> %s", processed, cumulative)
    return processed


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
