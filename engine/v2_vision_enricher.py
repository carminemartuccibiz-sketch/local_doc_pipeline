"""
V2 Stage 3 — Context-Aware Vision Enrichment.

Per ogni immagine in ``map.physical_assets.images`` con ``vision_processed=False``:
  1. Recupera i chunk collegati (``linked_chunks``)
  2. Costruisce il contesto testuale circostante (chunk precedente + successivo)
  3. Invia immagine + contesto al modello Vision (iniettato via ``VisionLLMCaller``)
  4. Scrive l'insight in ``enriched/chunk_NNN.enriched.md``
  5. Aggiorna map.json: ``vision_processed=True``, ``knowledge_state.vision_complete``
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from engine.project_memory import V2StagingLayout, setup_v2_staging_dirs
from engine.v2_map_manager import V2MapManager

logger = logging.getLogger(__name__)


class VisionLLMCaller(Protocol):
    """
    Contratto per chiamate Vision LLM — iniettabile, mockabile nei test.

    Accetta immagine (base64 PNG) + testo circostante, restituisce insight.
    """

    def __call__(
        self,
        *,
        image_b64: str,
        context_text: str,
        system_prompt: str,
        max_tokens: int,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class VisionEnrichmentResult:
    image_id: str
    insight: str
    linked_chunks: list[str]
    enriched_path: str


@dataclass
class V2VisionEnricher:
    """
    Arricchimento vision su immagini pending — processor opzionale Stage 3.

    ``vision_caller`` è iniettato dal workflow (LM Studio VL, Ollama, mock test).
    """

    layout: V2StagingLayout
    map_manager: V2MapManager
    vision_caller: VisionLLMCaller
    context_window_chars: int = 2000
    max_tokens: int = 512
    log_fn: Callable[[str], None] = field(default=lambda _m: None)

    SYSTEM_PROMPT = (
        "Sei un analista visivo tecnico. Descrivi ESATTAMENTE il contenuto "
        "dell'immagine nel contesto del documento. Focalizzati su: diagrammi "
        "architetturali, tabelle, screenshot UI, formule. Markdown conciso."
    )

    @classmethod
    def for_project(
        cls,
        slug: str,
        document_id: str,
        vision_caller: VisionLLMCaller,
        *,
        map_manager: V2MapManager | None = None,
        ensure_dirs: bool = True,
        log_fn: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> V2VisionEnricher:
        layout = setup_v2_staging_dirs(slug, document_id) if ensure_dirs else _layout_from_existing(
            slug, document_id
        )
        mgr = map_manager or V2MapManager(layout.map_json)
        return cls(
            layout,
            map_manager=mgr,
            vision_caller=vision_caller,
            log_fn=log_fn or (lambda _m: None),
            **kwargs,
        )

    def enrich_all_pending(
        self,
        *,
        stop_event: Any | None = None,
    ) -> list[VisionEnrichmentResult]:
        results: list[VisionEnrichmentResult] = []
        pending = [
            img for img in self.map_manager.list_images()
            if not img.get("vision_processed")
        ]

        for img in pending:
            if stop_event is not None and stop_event.is_set():
                self.log_fn("[VISION] Interrotto da stop_event")
                break
            result = self._enrich_single(img)
            if result is not None:
                results.append(result)

        if results:
            self.map_manager.update_metadata({"vision_enriched": True})
            self.map_manager.set_stage(
                "vision_enrichment",
                "completed",
                images_processed=len(results),
                images_pending=max(0, len(pending) - len(results)),
            )
            logger.info(
                "Vision enrichment completato: %d immagini in %s",
                len(results),
                self.layout.root,
            )

        return results

    def _enrich_single(self, img: dict[str, Any]) -> VisionEnrichmentResult | None:
        img_id = str(img.get("id") or "")
        rel_path = str(img.get("path") or "")
        img_path = self._resolve_asset_path(rel_path)
        if not img_path.is_file():
            self.log_fn(f"[VISION] Immagine non trovata: {img_path}")
            return None

        linked_chunks = list(img.get("linked_chunks") or [])
        context = self._build_context(linked_chunks)
        image_b64 = base64.b64encode(img_path.read_bytes()).decode()

        self.log_fn(f"[VISION] Enriching {img_id} ({len(context)} chars context)")
        try:
            insight = self.vision_caller(
                image_b64=image_b64,
                context_text=context,
                system_prompt=self.SYSTEM_PROMPT,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            self.log_fn(f"[VISION] Fallito {img_id}: {exc}")
            logger.debug("Vision enrichment fallito per %s", img_id, exc_info=True)
            return None

        insight = (insight or "").strip()
        if not insight:
            self.log_fn(f"[VISION] Risposta vuota per {img_id}")
            return None

        for chunk_id in linked_chunks:
            self._append_enriched_chunk(chunk_id, img_id, insight)
            self.map_manager.set_chunk_knowledge_state(chunk_id, vision_complete=True)

        self.map_manager.update_image(img_id, {"vision_processed": True})

        return VisionEnrichmentResult(
            image_id=img_id,
            insight=insight,
            linked_chunks=linked_chunks,
            enriched_path=str(self.layout.enriched),
        )

    def _resolve_asset_path(self, rel_path: str) -> Path:
        path = Path(rel_path)
        if path.is_absolute():
            return path
        return self.layout.root / rel_path

    def _append_enriched_chunk(self, chunk_id: str, img_id: str, insight: str) -> None:
        enriched_path = self.layout.enriched / f"{chunk_id}.enriched.md"
        enriched_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            enriched_path.read_text(encoding="utf-8") if enriched_path.is_file() else ""
        )
        block = f"\n\n### Vision insight — {img_id}\n\n{insight}\n"
        enriched_path.write_text(existing + block, encoding="utf-8", newline="\n")

    def _build_context(self, chunk_ids: list[str]) -> str:
        """Testo dei chunk collegati + vicini per contesto finestrato."""
        all_chunks = self.map_manager.list_chunks()
        if not all_chunks:
            return ""

        id_to_idx = {c["id"]: i for i, c in enumerate(all_chunks)}
        target_idxs = {id_to_idx[cid] for cid in chunk_ids if cid in id_to_idx}

        if not target_idxs and chunk_ids:
            return ""

        per_slice = max(1, self.context_window_chars // 3)
        context_parts: list[str] = []

        for idx in sorted(target_idxs):
            for offset in (-1, 0, 1):
                ci = idx + offset
                if ci < 0 or ci >= len(all_chunks):
                    continue
                chunk_path = self._resolve_asset_path(str(all_chunks[ci]["path"]))
                if not chunk_path.is_file():
                    continue
                text = chunk_path.read_text(encoding="utf-8")
                context_parts.append(text[:per_slice])

        return "\n\n---\n\n".join(dict.fromkeys(context_parts))


def _layout_from_existing(slug: str, document_id: str) -> V2StagingLayout:
    from engine.project_memory import v2_document_staging_dir

    root = v2_document_staging_dir(slug, document_id)
    return V2StagingLayout(
        root=root,
        original=root / "original",
        extracted_text=root / "extracted" / "text",
        extracted_images=root / "extracted" / "images",
        extracted_tables=root / "extracted" / "tables",
        extracted_ocr=root / "extracted" / "ocr",
        extracted_metadata=root / "extracted" / "metadata",
        chunks=root / "chunks",
        enriched=root / "enriched",
        rolling_memory=root / "rolling_memory",
        conflicts=root / "conflicts",
        conflicts_unresolved=root / "conflicts" / "unresolved",
        conflicts_resolved=root / "conflicts" / "resolved",
        audit=root / "audit",
        map_json=root / "map.json",
        conflict_log_json=root / "conflicts" / "conflict_log.json",
    )
