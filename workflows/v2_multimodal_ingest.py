"""
V2 multimodal ingest — orchestrazione pipeline fisica + stage opzionali.

Catena:
  Stage 1: PhysicalExtractor
  Stage 2: V2ChunkingManager
  Stage 3: V2VisionEnricher (opt-in: ``ctx['vision_caller']`` o ``V2_VISION_ENABLED``)
  Stage 4: V2RollingMemory (opt-in: ``ctx['fact_extractor']`` o ``V2_ROLLING_CONTEXT_ENABLED``)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from engine.project_memory import ingest_dir, setup_v2_staging_dirs
from engine.v2_chunking_manager import V2ChunkingManager
from engine.v2_map_manager import V2MapManager
from engine.v2_physical_extractor import RAW_EXTRACTED_NAME, PhysicalExtractor
from engine.v2_rolling_memory import FactExtractorLLM, V2RollingMemory
from engine.v2_vision_enricher import V2VisionEnricher, VisionLLMCaller
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.workflow_progress import report_phase

_WORKFLOW_TAG = "V2_INGEST"
_BASE_PHASES = 3
_V2_COMPAT_EXTENSIONS = frozenset({".pdf", ".md", ".txt"})


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _resolve_vision_caller(ctx: dict[str, Any]) -> VisionLLMCaller | None:
    """DI esplicita nel ctx ha priorità su env ``V2_VISION_ENABLED``."""
    injected = ctx.get("vision_caller")
    if injected is not None:
        return injected
    if _env_enabled("V2_VISION_ENABLED"):
        from core.ai_tasks import llm_complete_vision

        return llm_complete_vision
    return None


def _resolve_fact_extractor(ctx: dict[str, Any]) -> FactExtractorLLM | None:
    """DI esplicita nel ctx ha priorità su env ``V2_ROLLING_CONTEXT_ENABLED``."""
    injected = ctx.get("fact_extractor")
    if injected is not None:
        return injected
    if _env_enabled("V2_ROLLING_CONTEXT_ENABLED"):
        from core.ai_tasks import llm_extract_facts

        return llm_extract_facts
    return None


def discover_ingest_files(slug: str) -> list[Path]:
    """
    Scansione sicura di ``01_INGEST`` — nessun indexing ``[0]`` su glob vuoto.
    """
    root = ingest_dir(slug)
    if not root.is_dir():
        return []
    found: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() in _V2_COMPAT_EXTENSIONS:
            found.append(entry)
    return found


class V2MultimodalIngestWorkflow(BaseWorkflow):
    """
    Workflow beta — ingest multimodale V2.

    Per ogni file in ``01_INGEST`` (``.pdf``, ``.md``, ``.txt``):
      1. Scaffold ``02_STAGING/<doc_id>/``
      2. Copia sorgente in ``original/``
      3. ``PhysicalExtractor`` (PDF) o normalizzazione testo (md/txt)
      4. ``V2ChunkingManager`` → ``chunks/chunk_NNN.md`` + map.json
      5. ``V2VisionEnricher`` se opt-in (``V2_VISION_ENABLED=1`` o ``ctx['vision_caller']``)
      6. ``V2RollingMemory`` se opt-in (``V2_ROLLING_CONTEXT_ENABLED=1`` o ``ctx['fact_extractor']``)
    """

    capabilities = WorkflowCapabilities(
        requires_llm=False,
        requires_rag=False,
        supports_cancel=True,
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.run(file_path, ctx)

    def run_project(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Entry point sicuro: scansiona ``01_INGEST`` e processa ogni file compatibile.
        Se la cartella è vuota termina senza eccezioni (no IndexError).
        """
        slug = str(ctx.get("slug") or "").strip()
        if not slug:
            raise ValueError("ctx['slug'] richiesto")

        log_fn = ctx.get("log_fn") or (lambda _m: None)
        sources = discover_ingest_files(slug)

        if not sources:
            log_fn(f"[{_WORKFLOW_TAG}] Nessun file trovato")
            return {"status": "skipped", "reason": "no_files", "processed": 0}

        results: list[dict[str, Any]] = []
        for index, src in enumerate(sources):
            ctx["file_index"] = index
            ctx["files_in_job"] = len(sources)
            try:
                results.append(self.run(src, ctx))
            except ValueError as exc:
                log_fn(f"[{_WORKFLOW_TAG}] Skip {src.name}: {exc}")

        ok = sum(1 for r in results if r.get("status") == "ok")
        return {
            "status": "ok" if ok else "partial",
            "processed": ok,
            "total": len(sources),
            "results": results,
        }

    def run(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        slug = str(ctx.get("slug") or "").strip()
        if not slug:
            raise ValueError("ctx['slug'] richiesto")

        log_fn = ctx.get("log_fn") or (lambda _m: None)
        stop_event = ctx.get("stop_event")
        vision_caller = _resolve_vision_caller(ctx)
        fact_extractor = _resolve_fact_extractor(ctx)
        phases_total = _BASE_PHASES + int(vision_caller is not None) + int(
            fact_extractor is not None
        )

        def _check_stop() -> None:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError(f"{_WORKFLOW_TAG} interrotto da kill switch")

        src = Path(file_path)
        if not src.is_file():
            raise FileNotFoundError(f"File ingest non trovato: {src}")

        phase_no = 0

        def _report(label: str) -> None:
            nonlocal phase_no
            phase_no += 1
            report_phase(
                ctx,
                tag=_WORKFLOW_TAG,
                phase=phase_no,
                total=phases_total,
                label=label,
                file_path=src,
            )

        suffix = src.suffix.lower()
        if suffix not in _V2_COMPAT_EXTENSIONS:
            raise ValueError(
                f"Estensione non supportata in 01_INGEST: {src.name} "
                f"(ammesse: {', '.join(sorted(_V2_COMPAT_EXTENSIONS))})"
            )

        document_id = src.stem
        log_fn(f"[{_WORKFLOW_TAG}] Intake: {src.name} → doc_id={document_id}")

        _check_stop()
        _report("Setup staging V2 + map.json")
        layout = setup_v2_staging_dirs(slug, document_id)
        map_manager = V2MapManager(layout.map_json)
        map_manager.update_document(
            source_file=src.name,
            document_type="technical_document",
        )
        map_manager.set_stage("intake", "completed", source=str(src.name))

        dest = layout.original / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
            log_fn(f"[{_WORKFLOW_TAG}] Copiato in {dest.relative_to(layout.root)}")

        _check_stop()
        _report(
            "Physical extraction (PyMuPDF)"
            if suffix == ".pdf"
            else "Text normalization"
        )

        if suffix == ".pdf":
            extract_result = PhysicalExtractor(
                layout,
                map_manager=map_manager,
                log_fn=log_fn,
            ).extract(pdf_name=src.name)
            log_fn(
                f"[{_WORKFLOW_TAG}] Estrazione: {extract_result.page_count} pagine, "
                f"{len(extract_result.images)} immagini"
            )
        else:
            extract_result = _ingest_plain_text(layout, src, map_manager=map_manager)
            log_fn(
                f"[{_WORKFLOW_TAG}] Testo normalizzato: {extract_result.char_count} char"
            )

        _check_stop()
        _report("Physical semantic chunking")
        chunk_result = V2ChunkingManager(
            layout,
            map_manager=map_manager,
        ).chunk_raw_extract()

        vision_count = 0
        if vision_caller is not None:
            _check_stop()
            if _env_enabled("V2_VISION_ENABLED") and ctx.get("vision_caller") is None:
                log_fn(f"[{_WORKFLOW_TAG}] Vision attiva (V2_VISION_ENABLED)")
            _report("Context-aware vision enrichment")
            vision_results = V2VisionEnricher(
                layout,
                map_manager=map_manager,
                vision_caller=vision_caller,
                log_fn=log_fn,
            ).enrich_all_pending(stop_event=stop_event)
            vision_count = len(vision_results)
            log_fn(
                f"[{_WORKFLOW_TAG}] Vision: {vision_count} immagini arricchite"
            )

        rolling_count = 0
        if fact_extractor is not None:
            _check_stop()
            if _env_enabled("V2_ROLLING_CONTEXT_ENABLED") and ctx.get("fact_extractor") is None:
                log_fn(f"[{_WORKFLOW_TAG}] Rolling context attivo (V2_ROLLING_CONTEXT_ENABLED)")
            _report("Rolling context (facts-based memory)")
            rolling_count = V2RollingMemory(
                layout.rolling_memory,
                map_manager=map_manager,
                extractor=fact_extractor,
                log_fn=log_fn,
            ).process_all_chunks(layout, stop_event=stop_event)
            log_fn(
                f"[{_WORKFLOW_TAG}] Rolling: {rolling_count} chunk integrati"
            )

        log_fn(
            f"[{_WORKFLOW_TAG}] ✓ Completato: {chunk_result.chunk_count} chunk fisici "
            f"in 02_STAGING/{document_id}/"
        )
        return {
            "status": "ok",
            "document_id": document_id,
            "source": src.name,
            "staging_root": str(layout.root),
            "pages": extract_result.page_count,
            "images": len(extract_result.images),
            "chunks": chunk_result.chunk_count,
            "vision_enriched": vision_count,
            "rolling_processed": rolling_count,
            "raw_text": str(extract_result.raw_text_path),
        }


def _ingest_plain_text(
    layout,
    src: Path,
    *,
    map_manager: V2MapManager,
) -> Any:
    """Normalizza .md/.txt in raw_extracted.md (senza PyMuPDF)."""
    from engine.v2_physical_extractor import PhysicalExtractionResult

    body = src.read_text(encoding="utf-8", errors="replace")
    raw_path = layout.extracted_text / RAW_EXTRACTED_NAME
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_md = (
        f"# Raw extraction — {src.name}\n\n"
        f"> V2 text intake (no PDF). Source: `{src.name}`\n\n"
        f"## Page 1\n\n{body.strip()}\n"
    )
    raw_path.write_text(raw_md, encoding="utf-8", newline="\n")
    map_manager.update_metadata({"page_count": 1, "ocr_used": False})
    map_manager.set_stage(
        "physical_extraction",
        "completed",
        char_count=len(raw_md),
        image_count=0,
        mode="text",
    )
    return PhysicalExtractionResult(
        pdf_path=src,
        raw_text_path=raw_path,
        page_count=1,
        images=[],
        char_count=len(raw_md),
    )
