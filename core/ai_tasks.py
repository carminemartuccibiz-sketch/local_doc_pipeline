"""
Task AI — Gap Analysis iterativo (LM Studio / Ollama / OpenAI-compatible).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

from config import (
    LM_API_KEY,
    LM_MODEL,
    LM_MODEL_FALLBACK,
    LM_NATIVE_CHAT_URL,
    LM_OPENAI_BASE_URL,
    LM_TIMEOUT_S,
    LM_USE_NATIVE_CHAT,
)
from core.context_budget import ContextBundle, build_context_bundle
from core.report_metadata import build_gap_frontmatter, ensure_spec_document

logger = logging.getLogger(__name__)

# Modello LM Studio risolto a inizio sessione Gap Analysis (auto-discovery)
_session_lm_model: str | None = None

GAP_ANALYSIS_SYSTEM_PROMPT = """Agisci come un revisore di sistemi per DVAMOCLES SWORD™: Material Forge Studio®
e SIGNUM SENTINEL (documentazione tecnica PBR, pipeline, architettura).

Hai a disposizione la documentazione ufficiale SOT (Source of Truth) e un documento grezzo
con appunti o trascrizioni.

Il tuo UNICO compito è estrarre le informazioni presenti nel documento grezzo che:
- NON sono presenti nella documentazione SOT, oppure
- sono in CONTRADDIZIONE con la SOT.

REGOLE:
1. NON riassumere il grezzo. NON inventare feature.
2. Elenca meccaniche, parametri, moduli, workflow, naming, vincoli UI/UX mancanti o divergenti.
3. Usa sezioni: ## Mancanze rispetto alla SOT, ## Contraddizioni, ## Dettaglio per voce.
4. Per ogni voce indica riferimento al file grezzo (se fornito nel prompt).
5. Se non trovi gap, scrivi esplicitamente "_Nessuna mancanza rilevata in questo file_"."""


GAP_INTEGRATE_SYSTEM_PROMPT = """Agisci come curatore di un Gap Report cumulativo DVAMOCLES.

Ti viene il report gap ESISTENTE e le NUOVE scoperte da un altro file grezzo.
Integra le nuove voci senza perdere le precedenti: unisci duplicati, mantieni contraddizioni
in sezione dedicata, aggiungi timestamp di sessione se utile.

NON è una fusione narrativa: è un registro incrementale di lacune documentali.
Output: Markdown completo del report aggiornato (sostituisce il precedente)."""


class AIBackend(str, Enum):
    LM_STUDIO = "lm_studio"
    OLLAMA = "ollama"
    OPENAI = "openai"


def _backend() -> AIBackend:
    raw = os.environ.get("AI_BACKEND", "lm_studio").strip().lower()
    try:
        return AIBackend(raw)
    except ValueError:
        return AIBackend.LM_STUDIO


def _lm_host_from_openai_base(base_url: str) -> str:
    """http://localhost:1234/v1 -> http://localhost:1234"""
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        return u[:-3]
    return u.rsplit("/v1", 1)[0] or u


def _is_vl_or_vision_model(model_id: str) -> bool:
    low = model_id.lower()
    return any(k in low for k in ("vl", "vision", "multimodal", "mm-"))


def _model_sort_key(model_id: str) -> tuple[int, str]:
    """Priorita: testo-instruct > generico > VL (spesso 400 su /v1/chat/completions)."""
    if _is_embedding_model_id(model_id):
        return (99, model_id)
    if _is_vl_or_vision_model(model_id):
        return (10, model_id)
    if "instruct" in model_id.lower() or "chat" in model_id.lower():
        return (0, model_id)
    return (5, model_id)


def _prefer_native_lm_api(model_id: str) -> bool:
    if LM_USE_NATIVE_CHAT:
        return True
    return _is_vl_or_vision_model(model_id)


def _is_embedding_model_id(model_id: str) -> bool:
    low = model_id.lower()
    return any(
        k in low
        for k in ("embed", "embedding", "nomic-embed", "text-embedding", "e5-")
    )


def discover_lm_studio_model(
    *,
    base_url: str | None = None,
    force_refresh: bool = False,
) -> str:
    """
    Auto-discovery modello LM Studio: GET /v1/models (e API nativa /api/v1/models).
    Preferisce il modello con istanze caricate in memoria.
    """
    global _session_lm_model

    if _session_lm_model and not force_refresh:
        return _session_lm_model

    override = (LM_MODEL or os.environ.get("LM_STUDIO_MODEL", "")).strip()
    if override and override.lower() not in ("auto", "discover", ""):
        _session_lm_model = override
        logger.info("LM Studio modello (override .env): %s", override)
        return override

    openai_base = (base_url or LM_OPENAI_BASE_URL).rstrip("/")
    host = _lm_host_from_openai_base(openai_base)
    native_url = f"{host}/api/v1/models"
    models_url = f"{openai_base}/models"

    # 1) API nativa LM Studio — campo loaded_instances
    try:
        with httpx.Client(timeout=LM_TIMEOUT_S, headers=_auth_headers()) as client:
            r = client.get(native_url)
            if r.status_code == 200:
                payload = r.json()
                items = payload if isinstance(payload, list) else payload.get("models") or payload.get("data") or []
                loaded_ids: list[str] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    loaded = item.get("loaded_instances") or item.get("loadedInstances")
                    if loaded:
                        mid = (
                            item.get("id")
                            or item.get("key")
                            or item.get("model_key")
                            or item.get("name")
                        )
                        if mid and not _is_embedding_model_id(str(mid)):
                            loaded_ids.append(str(mid))
                if loaded_ids:
                    loaded_ids.sort(key=_model_sort_key)
                    _session_lm_model = loaded_ids[0]
                    api = "native" if _prefer_native_lm_api(_session_lm_model) else "openai"
                    logger.info(
                        "LM Studio modello (caricato): %s [API %s]",
                        _session_lm_model,
                        api,
                    )
                    if _is_vl_or_vision_model(_session_lm_model):
                        logger.warning(
                            "Modello VL rilevato: per gap testuale carica un LLM "
                            "instruct non-VL in LM Studio, oppure usa LM_USE_NATIVE_CHAT=true"
                        )
                    return _session_lm_model
    except httpx.RequestError as e:
        logger.debug("Native /api/v1/models non disponibile: %s", e)

    # 2) OpenAI-compatible GET /v1/models
    try:
        with httpx.Client(timeout=LM_TIMEOUT_S, headers=_auth_headers()) as client:
            r = client.get(models_url)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(
            f"Impossibile recuperare modelli da LM Studio ({models_url}): {e}"
        ) from e

    candidates: list[str] = []
    for m in data.get("data") or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid or _is_embedding_model_id(mid):
            continue
        state = str(m.get("state") or m.get("status") or "").lower()
        if state in ("loaded", "active", "running"):
            _session_lm_model = mid
            logger.info("LM Studio modello (stato=%s): %s", state, mid)
            return mid
        candidates.append(mid)

    if len(candidates) == 1:
        _session_lm_model = candidates[0]
        logger.info("LM Studio modello (unico disponibile): %s", _session_lm_model)
        return _session_lm_model

    if candidates:
        candidates.sort(key=_model_sort_key)
        _session_lm_model = candidates[0]
        logger.warning(
            "Piu modelli su LM Studio; scelto: %s (disponibili: %s)",
            _session_lm_model,
            ", ".join(candidates[:5]),
        )
        return _session_lm_model

    if LM_MODEL_FALLBACK:
        _session_lm_model = LM_MODEL_FALLBACK
        logger.warning(
            "Nessun modello rilevato; fallback: %s",
            _session_lm_model,
        )
        return _session_lm_model

    raise RuntimeError(
        "Nessun modello chat trovato su LM Studio. Carica un modello e avvia il server."
    )


def get_session_lm_model() -> str:
    if _session_lm_model:
        return _session_lm_model
    return discover_lm_studio_model()


def init_gap_analysis_session(*, require_allm: bool = True) -> str:
    """
    Pre-volo + auto-discovery modello. Da chiamare prima del loop su 01_RAW_INGEST.
    """
    from core.preflight import run_preflight_checks

    run_preflight_checks(require_lm=True, require_allm=require_allm)
    model = discover_lm_studio_model(force_refresh=True)
    print(f"  Modello LM Studio per questa sessione: {model}")
    return model


def _auth_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if LM_API_KEY:
        h["Authorization"] = f"Bearer {LM_API_KEY}"
    return h


def _complete_openai_compatible(
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=LM_TIMEOUT_S, headers=_auth_headers()) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"].strip()


def _complete_ollama(
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
) -> str:
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {"temperature": temperature},
    }
    with httpx.Client(timeout=LM_TIMEOUT_S) as client:
        r = client.post(f"{base}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


def _complete_lm_native(system_prompt: str, user_message: str, temperature: float) -> str:
    ctx = int(os.environ.get("LM_NATIVE_CONTEXT", "8192"))
    payload = {
        "model": get_session_lm_model(),
        "system_prompt": system_prompt,
        "input": user_message,
        "temperature": temperature,
        "context_length": ctx,
    }
    with httpx.Client(timeout=LM_TIMEOUT_S, headers=_auth_headers()) as client:
        r = client.post(LM_NATIVE_CHAT_URL, json=payload)
        r.raise_for_status()
        data = r.json()
    parts = []
    for block in data.get("output") or []:
        if isinstance(block, dict) and block.get("type") == "message":
            parts.append(str(block.get("content") or ""))
    return "\n".join(parts).strip()


def llm_complete(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    backend = _backend()
    if backend == AIBackend.OLLAMA:
        model = os.environ.get("OLLAMA_MODEL", "llama3.2")
        return _complete_ollama(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
        )
    if backend == AIBackend.OPENAI:
        base = os.environ.get("OPENAI_BASE_URL", LM_OPENAI_BASE_URL)
        model = os.environ.get("OPENAI_MODEL", "") or get_session_lm_model()
        return _complete_openai_compatible(
            base_url=base,
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    # lm_studio default
    model = get_session_lm_model()
    safe_max = min(
        max_tokens,
        int(os.environ.get("GAP_LM_MAX_OUTPUT", "2048")),
    )

    if _prefer_native_lm_api(model):
        try:
            return _complete_lm_native(system_prompt, user_message, temperature)
        except httpx.HTTPError as e:
            logger.warning("LM native fallita (%s), provo OpenAI-compatible", e)

    try:
        return _complete_openai_compatible(
            base_url=LM_OPENAI_BASE_URL,
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            max_tokens=safe_max,
        )
    except httpx.HTTPStatusError as e:
        detail = ""
        if e.response is not None:
            detail = e.response.text[:400]
        logger.warning(
            "LM Studio /v1/chat/completions HTTP %s — fallback native. %s",
            e.response.status_code if e.response else "?",
            detail,
        )
        return _complete_lm_native(system_prompt, user_message, temperature)


def perform_gap_analysis(
    *,
    sot_parts: list[tuple[str, str]],
    raw_rel_path: str,
    raw_body: str,
    existing_report: str = "",
    max_sot_chars: int | None = None,
    max_raw_chars: int | None = None,
    max_report_chars: int | None = None,
    integrate_report: bool = True,
    rag_context: str = "",
    sot_references: list[str] | None = None,
) -> tuple[str, ContextBundle, str]:
    """
    Confronto 1:1 grezzo vs SOT. Ritorna (markdown_da_scrivere, bundle_contesto).

    Se integrate_report=True e esiste report, chiede all'LLM di fondere.
    Altrimenti ritorna solo il delta del file corrente (append manuale a valle).
    """
    max_sot = max_sot_chars or int(os.environ.get("GAP_MAX_SOT_CHARS", "14000"))
    max_raw = max_raw_chars or int(os.environ.get("GAP_MAX_RAW_CHARS", "9000"))
    max_rep = max_report_chars or int(os.environ.get("GAP_MAX_REPORT_CHARS", "6000"))

    bundle = build_context_bundle(
        sot_parts=sot_parts,
        raw_label=raw_rel_path,
        raw_body=raw_body,
        existing_report=existing_report,
        max_sot_chars=max_sot,
        max_raw_chars=max_raw,
        max_report_chars=max_rep,
    )

    rag_section = f"\n\n{rag_context}\n" if rag_context.strip() else ""
    user_delta = f"""# Documentazione SOT (Source of Truth)

{bundle.sot_text}
{rag_section}
---

# Documento grezzo da confrontare

**File:** `{raw_rel_path}`

{bundle.raw_text}

---

Analizza SOLO gap e contraddizioni rispetto alla SOT. Output in Markdown strutturato."""

    delta_md = llm_complete(
        system_prompt=GAP_ANALYSIS_SYSTEM_PROMPT,
        user_message=user_delta,
        temperature=0.05,
        max_tokens=4096,
    )

    if not integrate_report or not existing_report.strip():
        section = _format_session_block(raw_rel_path, delta_md)
        return section, bundle, delta_md

    user_integrate = f"""# Gap Report esistente

{bundle.report_text}

---

# Nuove scoperte (file `{raw_rel_path}`)

{delta_md}

---

Produci il Gap Report COMPLETO aggiornato (Markdown)."""

    merged = llm_complete(
        system_prompt=GAP_INTEGRATE_SYSTEM_PROMPT,
        user_message=user_integrate,
        temperature=0.1,
        max_tokens=8192,
    )
    return merged, bundle, delta_md


def _format_session_block(raw_rel_path: str, body: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"\n\n---\n\n"
        f"## Sessione gap — {ts}\n\n"
        f"**File analizzato:** `{raw_rel_path}`\n\n"
        f"{body.strip()}\n"
    )


def append_to_report(
    report_path: str,
    section_md: str,
    *,
    source_file: str = "",
    sot_references: list[str] | None = None,
    raw_source: str | None = None,
) -> None:
    if raw_source is not None:
        source_file = raw_source
    from pathlib import Path

    p = Path(report_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        prev = p.read_text(encoding="utf-8", errors="replace")
        p.write_text(prev.rstrip() + "\n" + section_md, encoding="utf-8", newline="\n")
    else:
        header = build_gap_frontmatter(
            source_file=raw_source or "Gap_Report_Generale.md",
            sot_references=sot_references or [],
            title="Gap Report Generale",
        )
        intro = (
            "# Gap Report Generale — DVAMOCLES SWORD\n\n"
            "Registro incrementale: meccaniche/parametri nei documenti grezzi "
            "**assenti o in contrasto** con la documentazione SOT.\n"
        )
        p.write_text(header + intro + section_md, encoding="utf-8", newline="\n")


def write_gap_report(
    report_path: str,
    body_md: str,
    *,
    source_file: str,
    sot_references: list[str] | None = None,
    chunk_label: str | None = None,
    replace: bool = True,
) -> None:
    from pathlib import Path

    p = Path(report_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = body_md.strip()
    if not content.startswith("---"):
        content = ensure_spec_document(
            content,
            source_file=source_file,
            sot_references=sot_references or [],
            chunk_label=chunk_label,
        )
    p.write_text(content + "\n", encoding="utf-8", newline="\n")


def write_report(report_path: str, full_md: str) -> None:
    """Compatibilità: scrittura con frontmatter SPEC minimo."""
    write_gap_report(
        report_path,
        full_md,
        source_file="Gap_Report_Generale.md",
        sot_references=[],
        replace=True,
    )
