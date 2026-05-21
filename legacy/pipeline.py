"""
Orchestrazione: scan → convert → AnythingLLM → LM Studio → output.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from clients.anythingllm import AnythingLLMClient, AnythingLLMError
from config import (
    ACCEPTED_EXTENSIONS,
    DEFAULT_SOURCE_ROOT,
    EXCLUDE_DIR_NAMES,
    MAX_CHARS_PER_LM_CALL,
    MAX_FILE_BYTES,
    RAG_TOP_N,
    STATE_FILE,
    STAGING_DIR,
    WORKSPACE_SLUG,
    ensure_pipeline_dirs,
    output_dir,
)
from core.converters import ConvertResult, convert_file
from core.file_io import atomic_write_json
from clients.lm_studio import (
    EXTRACTOR_SYSTEM_PROMPT,
    LMStudioClient,
    LMStudioError,
    build_user_prompt,
    split_text,
)

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass
class PipelineState:
    version: int = 1
    source_root: str = ""
    workspace_slug: str = WORKSPACE_SLUG
    converted: dict[str, str] = field(default_factory=dict)  # rel → staging name
    uploaded: dict[str, str] = field(default_factory=dict)  # rel → allm location
    extracted: dict[str, str] = field(default_factory=dict)  # rel → output rel
    errors: list[dict[str, str]] = field(default_factory=list)
    updated_at: str = ""

    def save(self, path: Path = STATE_FILE) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
        atomic_write_json(path, asdict(self))

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> PipelineState:
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: data.get(k, v) for k, v in asdict(cls()).items()})


def should_skip_dir(path: Path) -> bool:
    return any(part in EXCLUDE_DIR_NAMES for part in path.parts)


def iter_source_files(
    source_root: Path,
    *,
    include_globs: list[str] | None = None,
) -> Iterator[Path]:
    if include_globs:
        seen: set[Path] = set()
        for pattern in include_globs:
            for p in source_root.glob(pattern):
                if p.is_file() and p.suffix.lower() in ACCEPTED_EXTENSIONS:
                    rp = p.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        yield p
        return

    for p in source_root.rglob("*"):
        if not p.is_file():
            continue
        if should_skip_dir(p.relative_to(source_root)):
            continue
        if p.suffix.lower() not in ACCEPTED_EXTENSIONS:
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            logger.warning("File troppo grande, skip: %s", p)
            continue
        yield p


def macro_key(rel_path: str) -> str:
    """Raggruppa per cartella di primo livello sotto root."""
    parts = Path(rel_path).parts
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else "root"


def step_scan_convert(
    source_root: Path,
    staging: Path,
    state: PipelineState,
    *,
    limit: int | None = None,
    include_globs: list[str] | None = None,
    force: bool = False,
) -> list[ConvertResult]:
    results: list[ConvertResult] = []
    count = 0
    for src in iter_source_files(source_root, include_globs=include_globs):
        rel = src.relative_to(source_root).as_posix()
        if not force and rel in state.converted:
            logger.debug("Già convertito: %s", rel)
            continue
        logger.info("[CONVERT] %s", rel)
        cr = convert_file(src, source_root, staging)
        results.append(cr)
        if cr.ok and cr.staging_path:
            state.converted[rel] = cr.staging_path.name
        else:
            state.errors.append({"step": "convert", "file": rel, "error": cr.error or "unknown"})
        count += 1
        if limit and count >= limit:
            break
    state.source_root = str(source_root)
    state.save()
    return results


def step_embed_anythingllm(
    source_root: Path,
    staging: Path,
    state: PipelineState,
    allm: AnythingLLMClient,
    *,
    limit: int | None = None,
    force: bool = False,
) -> None:
    slug = allm.ensure_workspace()
    state.workspace_slug = slug
    count = 0
    for rel, staging_name in list(state.converted.items()):
        if not force and rel in state.uploaded:
            continue
        staging_path = staging / staging_name
        if not staging_path.is_file():
            logger.warning("Staging mancante per %s", rel)
            continue
        logger.info("[EMBED] %s → AnythingLLM", rel)
        try:
            locs = allm.upload_document(
                staging_path,
                workspace_slug=slug,
                metadata={
                    "title": Path(rel).name,
                    "docSource": rel,
                    "description": "DVAMOCLES local_doc_pipeline staging",
                },
            )
            if locs:
                state.uploaded[rel] = locs[0]
            else:
                state.uploaded[rel] = staging_name
        except AnythingLLMError as e:
            logger.error("Upload fallito %s: %s", rel, e)
            state.errors.append({"step": "embed", "file": rel, "error": str(e)})
        count += 1
        if limit and count >= limit:
            break
    state.save()


def step_extract_lm(
    source_root: Path,
    staging: Path,
    out_dir: Path,
    state: PipelineState,
    allm: AnythingLLMClient,
    lm: LMStudioClient,
    *,
    limit: int | None = None,
    force: bool = False,
    use_rag: bool = True,
) -> None:
    slug = state.workspace_slug or WORKSPACE_SLUG
    count = 0
    for rel, staging_name in list(state.converted.items()):
        if not force and rel in state.extracted:
            continue
        staging_path = staging / staging_name
        if not staging_path.is_file():
            continue

        logger.info("[EXTRACT] %s", rel)
        try:
            local_text = staging_path.read_text(encoding="utf-8", errors="replace")
            query = (
                f"DVAMOCLES Material Forge Studio Signum Sentinel documentazione "
                f"architettura pipeline PBR: {rel}"
            )
            rag_chunks: list[dict[str, Any]] = []
            if use_rag and state.uploaded.get(rel):
                rag_chunks = allm.vector_search(slug, query, top_n=RAG_TOP_N)

            parts = split_text(local_text, MAX_CHARS_PER_LM_CALL)
            outputs: list[str] = []
            for i, part in enumerate(parts, 1):
                label = f"parte {i}/{len(parts)}" if len(parts) > 1 else None
                prompt = build_user_prompt(
                    rel_path=rel,
                    local_excerpt=part,
                    rag_chunks=rag_chunks if i == 1 else [],
                    part_label=label,
                )
                logger.info("  → LM Studio %s", label or "unica")
                chunk_out = lm.complete(user_message=prompt)
                outputs.append(chunk_out)

            merged = outputs[0] if len(outputs) == 1 else _merge_parts(outputs, rel)
            out_path = _output_path_for_rel(out_dir, rel)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            fm = (
                "---\n"
                f"author: DVAMOCLES\n"
                f"category: PRE_REFACTOR\n"
                f"source_file: \"{rel}\"\n"
                f"generated_by: local_doc_pipeline\n"
                f"generated_at: \"{datetime.now(UTC).isoformat()}\"\n"
                f"workspace: \"{slug}\"\n"
                "---\n\n"
            )
            out_path.write_text(fm + merged, encoding="utf-8", newline="\n")
            state.extracted[rel] = out_path.relative_to(out_dir).as_posix()
            logger.info("  ✓ scritto %s", state.extracted[rel])
        except LMStudioError as e:
            logger.error("LM fallito su %s: %s", rel, e)
            state.errors.append({"step": "extract", "file": rel, "error": str(e)})
        count += 1
        if limit and count >= limit:
            break
    state.save()


def _merge_parts(parts: list[str], rel: str) -> str:
    return (
        f"# Estrazione multi-parte: {rel}\n\n"
        + "\n\n---\n\n".join(f"## Parte {i}\n\n{p}" for i, p in enumerate(parts, 1))
    )


def _output_path_for_rel(out_dir: Path, rel: str) -> Path:
    stem = Path(rel).stem
    parent = Path(rel).parent
    safe = re.sub(r"[^\w\-.]+", "_", stem, flags=re.UNICODE)[:80]
    return out_dir / parent / f"{safe}__PRE_CLAUDE_EXTRACT.md"


def run_pipeline(
    *,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    steps: set[str],
    limit: int | None = None,
    force: bool = False,
    include_globs: list[str] | None = None,
    skip_allm: bool = False,
    skip_lm: bool = False,
    dry_run: bool = False,
) -> PipelineState:
    staging, out, _ = ensure_pipeline_dirs(source_root)
    state = PipelineState.load()
    if not state.source_root:
        state.source_root = str(source_root)

    if dry_run:
        files = list(iter_source_files(source_root, include_globs=include_globs))
        logger.info("DRY-RUN: %d file trovati (limit=%s)", len(files), limit)
        for p in files[: limit or 10]:
            logger.info("  - %s", p.relative_to(source_root))
        return state

    allm = AnythingLLMClient()
    lm = LMStudioClient()

    if "convert" in steps or "all" in steps:
        step_scan_convert(
            source_root, staging, state, limit=limit, include_globs=include_globs, force=force
        )

    if ("embed" in steps or "all" in steps) and not skip_allm:
        if not allm.health():
            raise AnythingLLMError(
                f"AnythingLLM non risponde su {AnythingLLMClient().base_url}. "
                "Avvia l'app desktop e abilita le API."
            )
        step_embed_anythingllm(source_root, staging, state, allm, limit=limit, force=force)

    if ("extract" in steps or "all" in steps) and not skip_lm:
        if not lm.health():
            raise LMStudioError(
                "LM Studio server non attivo. In LM Studio: Developer → Start Server."
            )
        models = lm.list_models()
        if models:
            logger.info("Modelli LM Studio: %s", ", ".join(models[:5]))
        step_extract_lm(
            source_root,
            staging,
            out,
            state,
            allm,
            lm,
            limit=limit,
            force=force,
            use_rag=not skip_allm,
        )

    logger.info(
        "Pipeline fine — convertiti=%d upload=%d estratti=%d errori=%d → %s",
        len(state.converted),
        len(state.uploaded),
        len(state.extracted),
        len(state.errors),
        out,
    )
    return state
