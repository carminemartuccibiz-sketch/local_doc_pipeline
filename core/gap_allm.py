"""
AnythingLLM: indicizzazione SOT + RAG per Gap Analysis.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from anythingllm_client import AnythingLLMClient, AnythingLLMError
from config import GAP_RAG_TOP_N, GAP_USE_ALLM_RAG, PIPELINE_ROOT
from core.file_io import atomic_write_json
from core.progress import progress_bar
from settings import ALLM_SOT_WORKSPACE_NAME, ALLM_SOT_WORKSPACE_SLUG

logger = logging.getLogger(__name__)

GAP_ALLM_STATE = PIPELINE_ROOT / "gap_allm_state.json"


def _load_state() -> dict[str, Any]:
    if GAP_ALLM_STATE.is_file():
        return json.loads(GAP_ALLM_STATE.read_text(encoding="utf-8"))
    return {"uploaded": {}}


def _save_state(state: dict[str, Any]) -> None:
    atomic_write_json(GAP_ALLM_STATE, state)


def resolve_sot_workspace_slug(client: AnythingLLMClient) -> str:
    """
    Slug reale dal server AnythingLLM (evita mismatch dvamocles-sot-canon vs dvamocles_sot_canon).
    """
    state = _load_state()
    cached = str(state.get("workspace_slug") or "").strip()
    workspaces = client.list_workspaces()

    if cached:
        for ws in workspaces:
            if ws.get("slug") == cached:
                return cached

    for ws in workspaces:
        if ws.get("name") == ALLM_SOT_WORKSPACE_NAME:
            slug = str(ws.get("slug") or "")
            if slug:
                logger.info("Workspace SOT trovato per nome: slug=%s", slug)
                state["workspace_slug"] = slug
                _save_state(state)
                return slug

    slug = client.ensure_workspace(
        name=ALLM_SOT_WORKSPACE_NAME,
        slug=ALLM_SOT_WORKSPACE_SLUG,
    )
    state["workspace_slug"] = slug
    _save_state(state)
    return slug


def sync_sot_to_anythingllm(
    sot_files: list[tuple[str, Path]],
    *,
    force: bool = False,
) -> str:
    if not GAP_USE_ALLM_RAG:
        return ""

    client = AnythingLLMClient()
    if not client.health():
        raise AnythingLLMError(
            "AnythingLLM non raggiungibile. Avvia il desktop app e verifica ANYTHINGLLM_BASE_URL."
        )

    slug = resolve_sot_workspace_slug(client)
    state = _load_state()
    uploaded: dict[str, str] = dict(state.get("uploaded") or {})

    if force or state.get("workspace_slug") != slug:
        uploaded = {}
        logger.info("Reset cache upload SOT (nuovo workspace slug=%s)", slug)

    adds: list[str] = []
    to_upload = [
        (rel, path)
        for rel, path in sot_files
        if path.suffix.lower() in {".md", ".markdown", ".txt"}
        and (force or rel not in uploaded)
    ]
    print(f"\nAnythingLLM SOT [{slug}]: indicizzazione {len(to_upload)} documenti LAST DOCS...")

    for rel, path in progress_bar(to_upload, desc="SOT upload", unit="doc"):
        try:
            locs = client.upload_document(
                path,
                workspace_slug=slug,
                metadata={
                    "title": path.name,
                    "docSource": rel,
                    "tier": "SOT",
                },
            )
            if locs:
                uploaded[rel] = locs[0]
                adds.append(locs[0])
                logger.info("SOT in AnythingLLM: %s", rel)
        except AnythingLLMError as e:
            logger.warning("Upload SOT fallito %s: %s", rel, e)

    if adds:
        client.update_embeddings(slug, adds=adds)
    state["uploaded"] = uploaded
    state["workspace_slug"] = slug
    _save_state(state)
    return slug


def fetch_sot_rag_context(
    *,
    workspace_slug: str,
    raw_rel: str,
    raw_excerpt: str,
    top_n: int | None = None,
) -> str:
    if not GAP_USE_ALLM_RAG or not workspace_slug:
        return ""

    client = AnythingLLMClient()
    if not client.health():
        logger.warning("AnythingLLM non disponibile — gap senza RAG")
        return ""

    slug = resolve_sot_workspace_slug(client)
    query = (
        f"DVAMOCLES Material Forge Studio gap analysis confronto LAST DOCS SOT: "
        f"{raw_rel}\n{raw_excerpt[:2500]}"
    )
    results = client.vector_search(
        slug,
        query,
        top_n=top_n or GAP_RAG_TOP_N,
    )
    if not results:
        return ""

    blocks: list[str] = ["## Contesto RAG (AnythingLLM — LAST DOCS)\n"]
    for i, hit in enumerate(results, 1):
        text = (hit.get("text") or hit.get("chunk") or "").strip()
        src = hit.get("title") or hit.get("url") or hit.get("id") or f"chunk-{i}"
        score = hit.get("score")
        score_s = f" (score={score:.3f})" if isinstance(score, (int, float)) else ""
        blocks.append(f"### RAG {i}: {src}{score_s}\n\n{text}\n")
    return "\n".join(blocks)
