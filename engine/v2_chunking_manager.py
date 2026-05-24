"""
V2 Stage 4 — Physical semantic chunking.

Legge ``raw_extracted.md``, applica ``core.chunking_v2.semantic_chunk``,
persiste chunk su disco e aggiorna map.json (linked list).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.chunking_v2 import SemanticChunk, semantic_chunk
from core.token_budget import resolve_chunk_max_tokens, resolve_token_limits
from engine.project_memory import V2StagingLayout, setup_v2_staging_dirs
from engine.v2_map_manager import V2MapManager
from engine.v2_physical_extractor import RAW_EXTRACTED_NAME

logger = logging.getLogger(__name__)

_IMG_REF_RE = re.compile(r"\[IMG_REF:(img_\d+)\]")
_PAGE_HEADING_RE = re.compile(r"^## Page (\d+)\s*$", re.MULTILINE)
_DEFAULT_MAX_TOKENS = 1200
_DEFAULT_MIN_TOKENS = 100


@dataclass(slots=True)
class V2ChunkingResult:
    raw_path: Path
    chunk_count: int
    chunk_paths: list[Path] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)


class V2ChunkingManager:
    """
    Chunking semantico V2 — output fisico in ``02_STAGING/<doc_id>/chunks/``.

    Usa ``semantic_chunk`` (heading tree, code fence atomici) e registra
    ogni chunk in ``map.json`` con ``previous_chunk`` / ``next_chunk``.
    """

    def __init__(
        self,
        layout: V2StagingLayout,
        *,
        map_manager: V2MapManager | None = None,
        max_tokens: int | None = None,
        min_tokens: int = _DEFAULT_MIN_TOKENS,
        model_hint: str | None = None,
    ) -> None:
        self.layout = layout
        self.map_manager = map_manager or V2MapManager(layout.map_json)
        self.min_tokens = min_tokens
        self.model_hint = model_hint or "cl100k_base"
        if max_tokens is not None:
            self.max_tokens = max_tokens
        else:
            try:
                limits = resolve_token_limits(self.model_hint)
                self.max_tokens = resolve_chunk_max_tokens(limits)
            except Exception:
                self.max_tokens = _DEFAULT_MAX_TOKENS

    @classmethod
    def for_project(
        cls,
        slug: str,
        document_id: str,
        *,
        ensure_dirs: bool = True,
        **kwargs: Any,
    ) -> V2ChunkingManager:
        layout = setup_v2_staging_dirs(slug, document_id) if ensure_dirs else _layout_from_existing(slug, document_id)
        return cls(layout, **kwargs)

    def chunk_raw_extract(
        self,
        raw_path: Path | str | None = None,
    ) -> V2ChunkingResult:
        path = Path(raw_path) if raw_path else self.layout.extracted_text / RAW_EXTRACTED_NAME
        if not path.is_file():
            raise FileNotFoundError(f"raw_extracted mancante: {path}")

        body = path.read_text(encoding="utf-8")
        semantic_chunks = semantic_chunk(
            body,
            max_tokens=self.max_tokens,
            min_tokens=self.min_tokens,
            model_hint=self.model_hint,
        )

        self.layout.chunks.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        chunk_paths: list[Path] = []

        for index, sch in enumerate(semantic_chunks, start=1):
            chunk_id = _format_chunk_id(index)
            rel_path = f"chunks/{chunk_id}.md"
            out_path = self.layout.chunks / f"{chunk_id}.md"
            out_path.write_text(sch.text.strip() + "\n", encoding="utf-8", newline="\n")

            assets = _extract_img_refs(sch.text)
            pages = _extract_page_numbers(sch.text)
            entry = {
                "id": chunk_id,
                "path": rel_path,
                "enriched_path": f"enriched/{chunk_id}.enriched.md",
                "section": sch.parent_heading or _infer_section(sch.text),
                "pages": pages,
                "char_count": len(sch.text),
                "token_estimate": sch.token_estimate,
                "previous_chunk": None,
                "next_chunk": None,
                "rolling_context_ref": None,
                "assets": assets,
                "semantic_tags": _semantic_tags_from_chunk(sch),
                "entities": [],
            }
            entries.append(entry)
            chunk_paths.append(out_path)

        registered = self.map_manager.set_chunks(entries)
        self._sync_image_links(registered)
        self.map_manager.set_stage(
            "physical_chunking",
            "completed",
            chunk_count=len(registered),
            max_tokens=self.max_tokens,
        )

        logger.info(
            "V2 physical chunking: %d chunk salvati in %s",
            len(registered),
            self.layout.chunks,
        )
        return V2ChunkingResult(
            raw_path=path,
            chunk_count=len(registered),
            chunk_paths=chunk_paths,
            chunks=registered,
        )

    def _sync_image_links(self, chunks: list[dict[str, Any]]) -> None:
        """Aggiorna ``linked_chunks`` sulle immagini già in physical_assets."""
        img_to_chunks: dict[str, list[str]] = {}
        for chunk in chunks:
            cid = str(chunk.get("id") or "")
            for asset_id in chunk.get("assets") or []:
                img_to_chunks.setdefault(str(asset_id), []).append(cid)

        for img_id, linked in img_to_chunks.items():
            existing = self.map_manager.get_image(img_id)
            if existing is None:
                continue
            merged = list(dict.fromkeys([*(existing.get("linked_chunks") or []), *linked]))
            self.map_manager.update_image(img_id, {"linked_chunks": merged})


def _format_chunk_id(seq: int) -> str:
    return f"chunk_{seq:03d}"


def _extract_img_refs(text: str) -> list[str]:
    return list(dict.fromkeys(_IMG_REF_RE.findall(text)))


def _extract_page_numbers(text: str) -> list[int]:
    return sorted({int(m) for m in _PAGE_HEADING_RE.findall(text)})


def _infer_section(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _semantic_tags_from_chunk(chunk: SemanticChunk) -> list[str]:
    tags: list[str] = []
    if chunk.has_code:
        tags.append("code")
    if chunk.has_table:
        tags.append("table")
    if chunk.boundary_type.name.startswith("H"):
        tags.append("heading")
    if _IMG_REF_RE.search(chunk.text):
        tags.append("image_ref")
    return tags


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
