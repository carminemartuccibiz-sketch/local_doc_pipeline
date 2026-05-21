"""
AnythingLLM: indicizzazione SOT + RAG per Gap Analysis.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from clients.anythingllm import AnythingLLMClient, AnythingLLMError
from config import GAP_RAG_TOP_N, GAP_USE_ALLM_RAG, PIPELINE_ROOT
from core.file_io import atomic_write_json
from core.paths import session_memory_dir
from core.progress import progress_bar
from config import ALLM_SOT_WORKSPACE_NAME, ALLM_SOT_WORKSPACE_SLUG

logger = logging.getLogger(__name__)

_LEGACY_STATE = session_memory_dir() / "gap_allm_state.json"

_memory_dir_override: Path | None = None


def set_gap_allm_memory_dir(path: Path | None) -> None:
    """UI progetto: state sotto projects/<slug>/04_MEMORY/."""
    global _memory_dir_override
    _memory_dir_override = path


# manual = nessuna chiamata API embed (incorpora dall'UI AnythingLLM — consigliato)
# per_file = un documento per richiesta update-embeddings (timeout lungo)
# batch = tutti insieme (può fallire al 60–70% per timeout)
def _embed_mode() -> str:
    return os.environ.get("ALLM_EMBED_MODE", "manual").strip().lower()


def gap_allm_state_path() -> Path:
    base = _memory_dir_override or session_memory_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "gap_allm_state.json"


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _normalize_uploaded_entry(entry: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(entry, dict):
        return {
            "location": str(entry.get("location") or ""),
            "md5": str(entry.get("md5") or ""),
        }
    return {"location": str(entry), "md5": ""}


def _load_state() -> dict[str, Any]:
    path = gap_allm_state_path()
    if not path.is_file() and _LEGACY_STATE.is_file():
        try:
            session_memory_dir().mkdir(parents=True, exist_ok=True)
            legacy_data = json.loads(_LEGACY_STATE.read_text(encoding="utf-8"))
            atomic_write_json(path, legacy_data)
            logger.info("Migrato gap_allm_state.json → 02_SESSION_MEMORY/")
        except OSError as e:
            logger.warning("Migrazione state ALLM fallita: %s", e)

    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"uploaded": {}}


def _save_state(state: dict[str, Any]) -> None:
    atomic_write_json(gap_allm_state_path(), state)


def clear_sot_sync_state() -> None:
    """Reset cache upload SOT (non cancella documenti nel workspace AnythingLLM)."""
    for p in (gap_allm_state_path(), _LEGACY_STATE):
        if p.is_file():
            p.unlink()
            logger.info("Rimosso %s", p)


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


def _normalize_match_name(s: str) -> str:
    return (
        s.lower()
        .replace("\\", "/")
        .replace("&", "and")
        .replace(" ", "_")
        .replace("-", "_")
    )


def _sot_in_workspace(rel: str, path: Path, remote_keys: set[str]) -> bool:
    """True se il documento SOT sembra già presente nel workspace (anche senza cache locale)."""
    rel_norm = rel.replace("\\", "/")
    if rel_norm in remote_keys:
        return True
    base = path.name
    stem = path.stem
    rel_tail = rel_norm.split("/")[-1]
    norm_stem = _normalize_match_name(stem)
    for key in remote_keys:
        kn = key.replace("\\", "/")
        if rel_norm in kn or rel_tail in kn or base in kn:
            return True
        if norm_stem and norm_stem in _normalize_match_name(kn):
            return True
    return False


def _sot_entry_current(
    uploaded: dict[str, Any],
    rel: str,
    path: Path,
) -> bool:
    ent = _normalize_uploaded_entry(uploaded.get(rel, ""))
    if not ent.get("location"):
        return False
    md5 = _file_md5(path)
    return ent.get("md5") == md5


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
    uploaded: dict[str, Any] = {
        k: _normalize_uploaded_entry(v)
        for k, v in dict(state.get("uploaded") or {}).items()
    }

    if force or state.get("workspace_slug") != slug:
        uploaded = {}
        logger.info("Reset cache upload SOT (force=%s, slug=%s)", force, slug)

    remote_keys: set[str] = set()
    try:
        remote_keys = client.list_workspace_document_keys(slug)
        if remote_keys:
            logger.info(
                "AnythingLLM: %d documenti già nel workspace (controllo duplicati)",
                len(remote_keys),
            )
    except Exception as e:
        logger.debug("Lista documenti workspace non disponibile: %s", e)

    to_upload: list[tuple[str, Path]] = []
    skipped = 0
    for rel, path in sot_files:
        if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            continue
        if not force and _sot_entry_current(uploaded, rel, path):
            skipped += 1
            continue
        if not force and (rel in remote_keys or _sot_in_workspace(rel, path, remote_keys)):
            prev = _normalize_uploaded_entry(uploaded.get(rel, ""))
            loc = prev.get("location") or "remote-existing"
            if embed_mode == "manual":
                loc = loc if loc != "remote-existing" else "manual-ui"
            uploaded[rel] = {
                "location": loc,
                "md5": _file_md5(path),
            }
            skipped += 1
            logger.debug("SOT già in workspace (skip upload): %s", rel)
            continue
        to_upload.append((rel, path))

    embed_mode = _embed_mode()
    logger.info("AnythingLLM SOT sync: embed_mode=%s", embed_mode)

    if not to_upload:
        rag_ok = False
        try:
            rag_ok = client.probe_vector_search(slug)
        except Exception:
            pass
        msg = (
            f"\nAnythingLLM SOT [{slug}]: nessun upload necessario "
            f"({skipped}/{len(sot_files)} già in cache)."
        )
        if rag_ok:
            msg += " RAG: OK (embedding pronti)."
        elif embed_mode == "manual":
            msg += (
                "\n  Se il RAG non risponde: in AnythingLLM apri workspace SOT → "
                "Documenti → Salva e incorpora (come hai fatto a mano)."
            )
        print(msg)
        state["uploaded"] = uploaded
        state["workspace_slug"] = slug
        state["embed_mode"] = embed_mode
        _save_state(state)
        return slug

    skip_api_upload = os.environ.get("ALLM_SOT_SKIP_API_UPLOAD", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if skip_api_upload or embed_mode == "manual":
        print(
            f"\nAnythingLLM SOT [{slug}]: modalità manuale — "
            f"NON chiamo update-embeddings via API (evita fail al 60–70%).\n"
            f"  In AnythingLLM: workspace «{ALLM_SOT_WORKSPACE_NAME}» → "
            f"carica/incorpora i {len(sot_files)} file LAST DOCS dall'UI.\n"
            f"  Poi rilancia la pipeline (upload API saltato: {len(to_upload)} pending in cache)."
        )
        for rel, path in to_upload:
            uploaded[rel] = {
                "location": "manual-ui",
                "md5": _file_md5(path),
            }
        state["uploaded"] = uploaded
        state["workspace_slug"] = slug
        state["embed_mode"] = embed_mode
        _save_state(state)
        if not client.probe_vector_search(slug):
            logger.warning(
                "RAG non ancora disponibile — completa «Salva e incorpora» in AnythingLLM"
            )
        return slug

    print(
        f"\nAnythingLLM SOT [{slug}]: upload {len(to_upload)} documenti "
        f"(embed_mode={embed_mode}, {skipped} saltati)."
    )

    embed_timeout = float(os.environ.get("ALLM_EMBED_TIMEOUT_S", "600"))
    embed_retries = int(os.environ.get("ALLM_EMBED_RETRIES", "1"))

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
            if not locs:
                logger.warning("Upload SOT senza location: %s", rel)
                continue

            uploaded[rel] = {
                "location": locs[0],
                "md5": _file_md5(path),
            }
            logger.info("SOT caricato: %s", rel)

            if embed_mode == "per_file":
                try:
                    client.update_embeddings(
                        slug,
                        adds=[locs[0]],
                        timeout_s=embed_timeout,
                        retries=embed_retries,
                    )
                    logger.info("Embedding OK: %s", rel)
                except Exception as e:
                    logger.warning(
                        "Embedding API fallito per %s (%s). "
                        "Incorpora manualmente in AnythingLLM.",
                        rel,
                        e,
                    )
        except AnythingLLMError as e:
            logger.warning("Upload SOT fallito %s: %s", rel, e)

    if embed_mode == "batch":
        adds = [
            loc
            for rel, _ in to_upload
            if (loc := str(_normalize_uploaded_entry(uploaded.get(rel, "")).get("location") or ""))
            and loc != "manual-ui"
        ]
        if adds:
            try:
                client.update_embeddings(
                    slug,
                    adds=adds,
                    timeout_s=embed_timeout,
                    retries=embed_retries,
                )
            except Exception as e:
                logger.warning(
                    "Batch update-embeddings fallito (%s). "
                    "Passa a ALLM_EMBED_MODE=manual o per_file nel .env",
                    e,
                )

    state["uploaded"] = uploaded
    state["workspace_slug"] = slug
    state["embed_mode"] = embed_mode
    _save_state(state)
    return slug


def fetch_sot_rag_context(
    *,
    workspace_slug: str,
    raw_rel: str,
    raw_excerpt: str,
    top_n: int | None = None,
    max_chars: int | None = None,
) -> str:
    if not GAP_USE_ALLM_RAG or not workspace_slug:
        return ""

    import os

    cap = max_chars or int(os.environ.get("GAP_RAG_MAX_CHARS", "3500"))

    client = AnythingLLMClient()
    if not client.health():
        logger.warning("AnythingLLM non disponibile — gap senza RAG")
        return ""

    slug = resolve_sot_workspace_slug(client)
    query = (
        f"DVAMOCLES Material Forge Studio Signum Sentinel gap analysis SOT "
        f"(LAST DOCS canonico + documentazione vecchia baseline): "
        f"{raw_rel}\n{raw_excerpt[:2000]}"
    )
    results = client.vector_search(
        slug,
        query,
        top_n=top_n or GAP_RAG_TOP_N,
    )
    if not results:
        return ""

    blocks: list[str] = ["## Contesto RAG (AnythingLLM — LAST DOCS)\n"]
    used = len(blocks[0])
    for i, hit in enumerate(results, 1):
        text = (hit.get("text") or hit.get("chunk") or "").strip()
        src = hit.get("title") or hit.get("url") or hit.get("id") or f"chunk-{i}"
        score = hit.get("score")
        score_s = f" (score={score:.3f})" if isinstance(score, (int, float)) else ""
        piece = f"### RAG {i}: {src}{score_s}\n\n{text}\n"
        if used + len(piece) > cap:
            blocks.append(
                f"_… altri {len(results) - i + 1} chunk RAG omessi (budget {cap} char)_\n"
            )
            break
        blocks.append(piece)
        used += len(piece)
    return "\n".join(blocks)
