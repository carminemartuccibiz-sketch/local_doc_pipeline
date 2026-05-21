"""
Task AI — Gap Analysis iterativo (LM Studio / Ollama / OpenAI-compatible).
"""
from __future__ import annotations

import logging
import os
import threading
import time
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
    PIPELINE_LM_COOLDOWN_S,
    PIPELINE_MAX_CONCURRENCY,
)
from core.context_budget import ContextBundle, build_context_bundle
from core.report_metadata import build_gap_frontmatter, ensure_spec_document
from core.token_budget import count_tokens

logger = logging.getLogger(__name__)

# Modello LM Studio risolto a inizio sessione Gap Analysis (auto-discovery)
_session_lm_model: str | None = None
_llm_semaphore = threading.Semaphore(
    max(1, PIPELINE_MAX_CONCURRENCY),
)

from core.gap_prompts import (
    GAP_ANALYSIS_SYSTEM_PROMPT,
    GAP_CONSOLIDATE_SYSTEM_PROMPT,
    GAP_INTEGRATE_SYSTEM_PROMPT,
)


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


def parse_lm_native_response(data: dict[str, Any]) -> str:
    """Parser unificato per risposte LM Studio API nativa."""
    parts: list[str] = []
    for block in data.get("output") or []:
        if isinstance(block, dict) and block.get("type") == "message":
            parts.append(str(block.get("content") or ""))
    text = "\n".join(parts).strip()
    if not text:
        for key in ("response", "content", "text"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                text = val.strip()
                break
    if not text:
        output = data.get("output")
        if isinstance(output, str) and output.strip():
            text = output.strip()
    return text


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
    text = parse_lm_native_response(data)
    if not text:
        raise RuntimeError(
            f"LM native: risposta vuota — payload: {str(data)[:300]}"
        )
    return text


def llm_complete(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    with _llm_semaphore:
        result = _llm_complete_unlocked(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if PIPELINE_LM_COOLDOWN_S > 0:
        time.sleep(PIPELINE_LM_COOLDOWN_S)
    return result


def _llm_complete_unlocked(
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

    est_in = count_tokens(system_prompt + user_message, model_hint=model)
    ctx_cap = int(os.environ.get("LM_NATIVE_CONTEXT", "0") or "0") or int(
        os.environ.get("GAP_MODEL_CONTEXT_TOKENS", "8192")
    )
    max_in = int(ctx_cap * 0.72) - safe_max
    if est_in > max_in > 2000:
        from core.context_budget import truncate_middle

        user_message, _ = truncate_middle(
            user_message,
            max(2000, max_in - count_tokens(system_prompt, model_hint=model)),
            "prompt_utente",
        )
        logger.warning(
            "Prompt ridotto per contesto LM (%d → budget ~%d token input)",
            est_in,
            max_in,
        )

    if _prefer_native_lm_api(model):
        try:
            return _complete_lm_native(system_prompt, user_message, temperature)
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 500:
                body = e.response.text[:200]
                if "context" in body.lower():
                    raise RuntimeError(
                        "LM Studio: contesto superato. Riduci GAP_RAW_INPUT_TOKEN_BUDGET "
                        "o GAP_RAG_MAX_CHARS in .env, oppure aumenta loaded context nel modello."
                    ) from e
            logger.warning("LM native fallita (%s), provo OpenAI-compatible", e)
        except httpx.HTTPError as e:
            logger.warning("LM native fallita (%s), provo OpenAI-compatible", e)

    return _complete_openai_compatible(
        base_url=LM_OPENAI_BASE_URL,
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=safe_max,
    )


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
    chunk_index: int | None = None,
    chunks_total: int | None = None,
    chunk_label: str | None = None,
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
    chunk_scope = ""
    if chunks_total is not None and chunks_total > 0 and chunk_index is not None:
        part_n = chunk_index + 1
        lab = chunk_label or "sezione"
        chunk_scope = (
            f"\n> **Ambito (chunking):** Parte **{part_n}/{chunks_total}** del file "
            f"(`{lab}`). Analizza **solo** il testo sotto; altre parti = altre passate LLM.\n"
        )
    user_delta = f"""# Documentazione SOT (Source of Truth)

{bundle.sot_text}
{rag_section}
---

# Documento grezzo da confrontare

**File:** `{raw_rel_path}`
{chunk_scope}
{bundle.raw_text}

---

Report **ricco e contestualizzato** per allegarlo a Claude/GPT e aggiornare LAST DOCS.
Rispetta la struttura del system prompt (Sintesi, GAP numerati, Azioni redazione, Handoff IA)."""

    out_budget = int(os.environ.get("GAP_LM_MAX_OUTPUT", "1536"))
    delta_md = llm_complete(
        system_prompt=GAP_ANALYSIS_SYSTEM_PROMPT,
        user_message=user_delta,
        temperature=0.05,
        max_tokens=out_budget,
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


def consolidate_gap_report_for_handoff(
    *,
    rel_key: str,
    merged_chunk_report: str,
    sot_refs: list[str],
    chunks_total: int,
) -> str:
    """
    Unisce analisi multi-chunk in un report unico, ricco, pronto per Claude/GPT.
    """
    if not merged_chunk_report.strip():
        return merged_chunk_report
    if chunks_total <= 1 and os.environ.get("GAP_REPORT_CONSOLIDATE_SINGLE", "").lower() not in (
        "1",
        "true",
        "yes",
    ):
        return merged_chunk_report

    user = f"""# File grezzo
`{rel_key}` — analizzato in **{chunks_total}** parti (chunk).

# Riferimenti SOT consultati
{chr(10).join(f"- {r}" for r in (sot_refs or [])[:30])}

---

# Report per chunk (da consolidare senza perdere contesto)

{merged_chunk_report[: int(os.environ.get("GAP_MAX_REPORT_CHARS", "12000"))]}

---

Produci il report consolidato unico, ricco, pronto per handoff a Claude/GPT."""
    out_budget = int(os.environ.get("GAP_LM_MAX_OUTPUT", "1536"))
    consolidate_budget = int(
        os.environ.get("GAP_LM_MAX_OUTPUT_CONSOLIDATE", str(min(2500, out_budget * 2)))
    )
    return llm_complete(
        system_prompt=GAP_CONSOLIDATE_SYSTEM_PROMPT,
        user_message=user,
        temperature=0.08,
        max_tokens=consolidate_budget,
    )


def _format_session_block(raw_rel_path: str, body: str) -> str:
    """Compat: usa format_general_report_entry per il report cumulativo."""
    return _format_general_report_entry(raw_rel_path, body)


def _format_general_report_entry(raw_rel_path: str, body: str) -> str:
    """Voce nel Gap_Report_Generale.md — unica destinazione handoff Claude/GPT."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"\n\n---\n\n"
        f"## File grezzo: `{raw_rel_path}`\n\n"
        f"_Registrato: {ts} — sezione da usare con LAST DOCS per aggiornamento canonico._\n\n"
        f"{body.strip()}\n"
    )


def append_to_report(
    report_path: str,
    section_md: str,
    *,
    source_file: str = "",
    sot_references: list[str] | None = None,
    raw_source: str | None = None,
    sot_tiers: str | None = None,
    is_general_report: bool = False,
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
        tiers = sot_tiers or (
            "1,2"
            if os.environ.get("GAP_SOT_LAST_DOCS_ONLY", "true").lower()
            not in ("1", "true", "yes")
            else "1"
        )
        header = build_gap_frontmatter(
            source_file=raw_source or "Gap_Report_Generale.md",
            sot_references=sot_references or [],
            title="Gap Report Generale — handoff aggiornamento LAST DOCS",
            sot_tiers=tiers,
        )
        intro = (
            "# Gap Report Generale — DVAMOCLES SWORD\n\n"
            "**Documento unico da allegare a Claude o GPT** per aggiornare la suite "
            "`LAST DOCS/` (tier 1 vince su documentazione vecchia).\n\n"
            "Ogni sezione `## File grezzo:` corrisponde a un materiale in "
            "`01_RAW_INGEST/`. Usa **Sintesi**, **GAP-XX**, **Azione di redazione** "
            "e **Handoff IA** in ogni sezione.\n\n"
            "## Indice file (cresce ad ogni run)\n\n"
            "_Le voci compaiono sotto in ordine di analisi._\n"
        )
        p.write_text(header + intro + section_md, encoding="utf-8", newline="\n")


def write_gap_report(
    report_path: str,
    body_md: str,
    *,
    source_file: str,
    sot_references: list[str] | None = None,
    chunk_label: str | None = None,
    chunks_total: int | None = None,
    chunk_labels: list[str] | None = None,
    sot_tiers: str | None = None,
    replace: bool = True,
) -> None:
    from pathlib import Path

    p = Path(report_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not replace and p.is_file():
        return
    content = body_md.strip()
    if not content.startswith("---"):
        content = ensure_spec_document(
            content,
            source_file=source_file,
            sot_references=sot_references or [],
            chunk_label=chunk_label,
            chunks_total=chunks_total,
            chunk_labels=chunk_labels,
            sot_tiers=sot_tiers,
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
