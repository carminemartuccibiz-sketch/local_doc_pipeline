"""
V2 multimodal ingest — orchestrazione pipeline fisica (beta).

Catena: intake 01_INGEST → staging V2 → PhysicalExtractor → V2ChunkingManager.
Fase LLM (Vision / Rolling Context) — placeholder Prompt 6+.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from engine.project_memory import ingest_dir, setup_v2_staging_dirs
from engine.v2_chunking_manager import V2ChunkingManager
from engine.v2_map_manager import V2MapManager
from engine.v2_physical_extractor import RAW_EXTRACTED_NAME, PhysicalExtractor
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.workflow_progress import report_phase

_WORKFLOW_TAG = "V2_INGEST"
_PHASES_TOTAL = 3
_V2_COMPAT_EXTENSIONS = frozenset({".pdf", ".md", ".txt"})


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
    Workflow beta — ingest multimodale V2 senza chiamate LLM (fase 1).

    Per ogni file in ``01_INGEST`` (``.pdf``, ``.md``, ``.txt``):
      1. Scaffold ``02_STAGING/<doc_id>/``
      2. Copia sorgente in ``original/``
      3. ``PhysicalExtractor`` (PDF) o normalizzazione testo (md/txt)
      4. ``V2ChunkingManager`` → ``chunks/chunk_NNN.md`` + map.json
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

        def _check_stop() -> None:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError(f"{_WORKFLOW_TAG} interrotto da kill switch")

        src = Path(file_path)
        if not src.is_file():
            raise FileNotFoundError(f"File ingest non trovato: {src}")

        suffix = src.suffix.lower()
        if suffix not in _V2_COMPAT_EXTENSIONS:
            raise ValueError(
                f"Estensione non supportata in 01_INGEST: {src.name} "
                f"(ammesse: {', '.join(sorted(_V2_COMPAT_EXTENSIONS))})"
            )

        document_id = src.stem
        log_fn(f"[{_WORKFLOW_TAG}] Intake: {src.name} → doc_id={document_id}")

        _check_stop()
        report_phase(
            ctx,
            tag=_WORKFLOW_TAG,
            phase=1,
            total=_PHASES_TOTAL,
            label="Setup staging V2 + map.json",
            file_path=src,
        )
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
        report_phase(
            ctx,
            tag=_WORKFLOW_TAG,
            phase=2,
            total=_PHASES_TOTAL,
            label="Physical extraction (PyMuPDF)" if suffix == ".pdf" else "Text normalization",
            file_path=src,
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
        report_phase(
            ctx,
            tag=_WORKFLOW_TAG,
            phase=3,
            total=_PHASES_TOTAL,
            label="Physical semantic chunking",
            file_path=src,
        )
        chunk_result = V2ChunkingManager(
            layout,
            map_manager=map_manager,
        ).chunk_raw_extract()

        # ------------------------------------------------------------------
        # TODO: Fase 2 — Vision e Rolling Context
        # - Per ogni immagine in map.physical_assets.images con vision_processed=False:
        #     chiamare LLM/Vision con chunk collegato + rolling_memory strutturato
        # - Scrivere enriched/chunk_NNN.enriched.md
        # - Aggiornare rolling_memory/rolling_state.json via merge_rolling_structured
        # - Impostare rolling_context_ref su ogni chunk in map.json
        # - knowledge_state.vision_complete / facts_extracted
        # ------------------------------------------------------------------

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
