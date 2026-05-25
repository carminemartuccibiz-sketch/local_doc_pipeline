"""
Task AI — Gap Analysis iterativo (LM Studio / Ollama / OpenAI-compatible).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator

import httpx

from clients.http_helpers import lm_request
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
from core.llm_errors import LLMFatalError, LLMRecoverableError, classify_llm_error
from core.token_budget import (
    count_tokens,
    resolve_token_limits,
    validate_request_budget,
)

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


def _orchestrator_state():
    from engine.orchestrator import get_orchestrator_state

    return get_orchestrator_state()


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def build_lm_http_timeout(*, read_seconds: float | None = None) -> httpx.Timeout:
    """
    Timeout granulari (GPT/Perplexity audit) — evita thread bloccati su LM Studio OOM/freeze.

    - connect: fallisce rapido se LM Studio non risponde
    - read: cap separato (default 120s) sotto LM_TIMEOUT_S
    - timeout: tetto assoluto della richiesta (= LM_TIMEOUT_S)
    """
    ceiling = float(LM_TIMEOUT_S)
    read = read_seconds if read_seconds is not None else min(
        _float_env("LM_READ_TIMEOUT_S", 120.0),
        ceiling,
    )
    return httpx.Timeout(
        connect=_float_env("LM_CONNECT_TIMEOUT_S", 10.0),
        read=read,
        write=_float_env("LM_WRITE_TIMEOUT_S", 30.0),
        pool=_float_env("LM_POOL_TIMEOUT_S", 30.0),
        timeout=ceiling,
    )


_lm_pool_register_lock = threading.Lock()
_pooled_lm_registered = False


def _reset_pooled_lm_registration() -> None:
    global _pooled_lm_registered
    with _lm_pool_register_lock:
        _pooled_lm_registered = False


def _ensure_pooled_lm_registered(client: httpx.Client) -> None:
    """Registra il client pool una sola volta per kill_all (Perplexity §1.1)."""
    global _pooled_lm_registered
    with _lm_pool_register_lock:
        if _pooled_lm_registered or client.is_closed:
            return
        _orchestrator_state().register_client(client)
        _pooled_lm_registered = True


def _close_client_absolutely(client: httpx.Client, *, owned: bool) -> None:
    """Chiusura idempotente — solo client ad-hoc; il pool si chiude in kill_all."""
    if not owned:
        return
    state = _orchestrator_state()
    state.unregister_client(client)
    try:
        if not client.is_closed:
            client.close()
    except Exception:
        pass


def release_lm_http_resources() -> None:
    """
    Invalida pool LM dopo timeout/OOM/kill (chiamabile da job_runner).
    Il kill switch in orchestrator chiude anche i client registrati.
    """
    from clients.http_pool import close_all_http_clients

    closed = close_all_http_clients()
    _reset_pooled_lm_registration()
    if closed:
        logger.info("Pool HTTP LM invalidato (%d client)", closed)


def abort_if_stop_requested() -> None:
    """Cooperativo con Kill Switch — da chiamare tra step job lunghi."""
    _check_kill_switch()


def _check_kill_switch() -> None:
    if _orchestrator_state().stop_event.is_set():
        raise InterruptedError("Pipeline fermata dall'utente (Kill Switch)")


def _normalize_client_kwargs(kwargs: dict[str, Any]) -> tuple[dict[str, Any], httpx.Timeout]:
    timeout = kwargs.pop("timeout", None)
    if timeout is None:
        t = build_lm_http_timeout()
    elif isinstance(timeout, httpx.Timeout):
        t = timeout
    else:
        read = float(timeout)
        t = build_lm_http_timeout(read_seconds=read)
    kwargs["timeout"] = t
    return kwargs, t


def _lm_request_guarded(
    client: httpx.Client,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """lm_request con timeout per-request e release pool su stall (OOM)."""
    state = _orchestrator_state()
    kwargs.setdefault("timeout", build_lm_http_timeout())
    try:
        return lm_request(client, method, url, **kwargs)
    except httpx.TimeoutException as e:
        release_lm_http_resources()
        raise RuntimeError(
            f"LM Studio non ha risposto in tempo ({type(e).__name__}) — "
            "possibile OOM/GPU freeze; riprova o riduci il batch"
        ) from e
    except httpx.HTTPError as e:
        if state.stop_event.is_set():
            release_lm_http_resources()
            raise InterruptedError("Richiesta annullata da Kill Switch") from e
        raise


@contextmanager
def _managed_httpx_client(**kwargs: Any) -> Iterator[httpx.Client]:
    """
    Client httpx — pool condiviso (non chiuso qui); ad-hoc solo con kwargs espliciti.

    Il pool è registrato una volta su OrchestratorState per kill_all livello 2.
    """
    from clients.http_pool import HTTP_LIMITS, get_lm_client

    _check_kill_switch()
    owns_client = bool(kwargs)
    kwargs, _ = _normalize_client_kwargs(dict(kwargs))

    if owns_client:
        headers = kwargs.pop("headers", None)
        client = httpx.Client(
            timeout=kwargs.pop("timeout", build_lm_http_timeout()),
            limits=HTTP_LIMITS,
            headers=headers,
            **kwargs,
        )
        _orchestrator_state().register_client(client)
    else:
        client = get_lm_client()
        _ensure_pooled_lm_registered(client)

    try:
        yield client
    except httpx.TimeoutException:
        release_lm_http_resources()
        raise
    except httpx.HTTPError as e:
        if _orchestrator_state().stop_event.is_set():
            release_lm_http_resources()
            raise InterruptedError("Richiesta annullata da Kill Switch") from e
        raise
    finally:
        _close_client_absolutely(client, owned=owns_client)


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
        with _managed_httpx_client() as client:
            r = _lm_request_guarded(
                client,
                "GET",
                native_url,
                headers=_auth_headers(),
                timeout=build_lm_http_timeout(read_seconds=30.0),
            )
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
        with _managed_httpx_client() as client:
            r = _lm_request_guarded(
                client,
                "GET",
                models_url,
                headers=_auth_headers(),
                timeout=build_lm_http_timeout(read_seconds=30.0),
            )
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


def set_session_lm_model(model_id: str, *, persist_env: bool = True) -> str:
    """Imposta modello per la sessione UI (dopo ModelRouter)."""
    global _session_lm_model
    mid = model_id.strip()
    if not mid:
        raise ValueError("model_id vuoto")
    _session_lm_model = mid
    if persist_env:
        os.environ["LM_STUDIO_MODEL"] = mid
    logger.info("Modello sessione: %s", mid)
    return mid


def init_gap_analysis_session(
    *,
    require_allm: bool = True,
    force_refresh: bool = False,
) -> str:
    """
    Pre-volo + auto-discovery modello. Da chiamare prima del loop su 01_RAW_INGEST.
    Se il modello è già impostato (ModelRouter), non forzare refresh salvo richiesta.
    """
    from core.preflight import run_preflight_checks

    run_preflight_checks(require_lm=True, require_allm=require_allm)
    model = discover_lm_studio_model(force_refresh=force_refresh)
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
    with _managed_httpx_client() as client:
        r = _lm_request_guarded(
            client,
            "POST",
            url,
            json=payload,
            headers=_auth_headers(),
        )
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
    with _managed_httpx_client() as client:
        r = _lm_request_guarded(client, "POST", f"{base}/api/chat", json=payload)
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
    with _managed_httpx_client() as client:
        r = _lm_request_guarded(
            client,
            "POST",
            LM_NATIVE_CHAT_URL,
            json=payload,
            headers=_auth_headers(),
        )
        r.raise_for_status()
        data = r.json()
    text = parse_lm_native_response(data)
    if not text:
        raise RuntimeError(
            f"LM native: risposta vuota — payload: {str(data)[:300]}"
        )
    return text


def parse_fallback_chain() -> list[str]:
    raw = os.environ.get("LM_FALLBACK_CHAIN", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    try:
        return [get_session_lm_model()]
    except Exception:
        return [LM_MODEL or LM_MODEL_FALLBACK or "local-model"]


def _truncate_user_for_context(
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    max_output_tokens: int,
    safety_margin: int = 128,
) -> str:
    limits = resolve_token_limits(model)
    max_in = (
        limits.context_tokens
        - limits.response_reserve
        - max_output_tokens
        - safety_margin
    )
    est_in = count_tokens(system_prompt + user_message, model_hint=model)
    if est_in <= max_in or max_in < 256:
        return user_message
    from core.context_budget import truncate_middle

    user_message, _ = truncate_middle(
        user_message,
        max(256, max_in - count_tokens(system_prompt, model_hint=model))
        * 4,
        "prompt_utente",
    )
    logger.warning(
        "Prompt ridotto per contesto LM (%d → budget ~%d token input)",
        est_in,
        max_in,
    )
    return user_message


def smart_llm_complete(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """Hierarchical model fallback — opt-in via LM_USE_SMART_FALLBACK."""
    last_error: Exception | None = None
    current_max = max_tokens
    current_user = user_message
    chain = parse_fallback_chain()

    for model_id in chain:
        try:
            budget = validate_request_budget(
                model_id=model_id,
                system_prompt=system_prompt,
                user_prompt=current_user,
                reserved_output=current_max,
            )
            if not budget["fits"] and budget["usable"] > 0:
                ratio = min(0.7, budget["usable"] / max(budget["projected"], 1))
                current_user = current_user[: max(256, int(len(current_user) * ratio))]

            prev = get_session_lm_model()
            set_session_lm_model(model_id, persist_env=False)
            try:
                return _llm_complete_unlocked(
                    system_prompt=system_prompt,
                    user_message=current_user,
                    temperature=temperature,
                    max_tokens=current_max,
                )
            finally:
                set_session_lm_model(prev, persist_env=False)

        except Exception as e:
            classified = classify_llm_error(e)
            last_error = classified
            logger.warning("smart_llm_complete fail model=%s: %s", model_id, classified)
            if isinstance(classified, LLMFatalError):
                raise classified from e
            current_max = int(current_max * 0.7)
            time.sleep(2)

    raise RuntimeError(f"All fallback models failed: {last_error}")


def llm_complete(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    if os.environ.get("LM_USE_SMART_FALLBACK", "").lower() in ("1", "true", "yes"):
        with _llm_semaphore:
            result = smart_llm_complete(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
            )
    else:
        with _llm_semaphore:
            result = _llm_complete_unlocked(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
            )
    from engine.cooldown_manager import get_cooldown_manager

    get_cooldown_manager().after_llm_call(_orchestrator_state().stop_event)
    return result


def _llm_complete_unlocked(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    from engine.llm_watchdog import run_with_llm_watchdog

    def _run() -> str:
        return _llm_complete_unlocked_inner(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    return run_with_llm_watchdog(_run, label="complete")


def _llm_complete_unlocked_inner(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    _check_kill_switch()
    backend = _backend()
    if backend == AIBackend.OLLAMA:
        model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    elif backend == AIBackend.OPENAI:
        model = os.environ.get("OPENAI_MODEL", "") or get_session_lm_model()
    else:
        model = get_session_lm_model()
    est_tok = count_tokens(system_prompt + user_message, model_hint=model)
    _orchestrator_state().emit_log(f"LLM call: {model} ~{est_tok} tok")

    if backend == AIBackend.OLLAMA:
        return _complete_ollama(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
        )
    if backend == AIBackend.OPENAI:
        base = os.environ.get("OPENAI_BASE_URL", LM_OPENAI_BASE_URL)
        return _complete_openai_compatible(
            base_url=base,
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    # lm_studio default
    safe_max = min(
        max_tokens,
        int(os.environ.get("GAP_LM_MAX_OUTPUT", "2048")),
    )

    user_message = _truncate_user_for_context(
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        max_output_tokens=safe_max,
    )

    if _prefer_native_lm_api(model):
        try:
            return _complete_lm_native(system_prompt, user_message, temperature)
        except httpx.TimeoutException as e:
            release_lm_http_resources()
            raise RuntimeError(
                "LM Studio timeout (native API) — possibile OOM/GPU freeze"
            ) from e
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


# --- V2 multimodal ingest (Vision + rolling facts) ---

V2_FACT_EXTRACT_SYSTEM_PROMPT = """Sei un estrattore di fatti strutturati per documentazione tecnica.
Rispondi SOLO con JSON valido (nessun markdown fence), schema:
{
  "facts": [{"claim": "...", "section": "...", "confidence": "high|medium|low"}],
  "entities": ["..."],
  "decisions": ["..."]
}
Estrai solo affermazioni verificabili dal testo; non inventare."""


def _vision_model_id() -> str:
    explicit = (
        os.environ.get("V2_VISION_MODEL", "").strip()
        or os.environ.get("LM_VISION_MODEL", "").strip()
    )
    if explicit:
        return explicit
    model = get_session_lm_model()
    if _is_vl_or_vision_model(model):
        return model
    return model


def _complete_openai_vision(
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    image_b64: str,
    context_text: str,
    max_tokens: int,
    temperature: float = 0.1,
) -> str:
    """Chat completions multimodale (LM Studio VL / OpenAI-compatible)."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    user_parts: list[dict[str, Any]] = []
    if context_text.strip():
        user_parts.append(
            {
                "type": "text",
                "text": f"Contesto documento circostante:\n\n{context_text.strip()}",
            }
        )
    user_parts.append(
        {
            "type": "text",
            "text": "Descrivi il contenuto dell'immagine nel contesto sopra.",
        }
    )
    user_parts.append(
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        }
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_parts},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with _managed_httpx_client() as client:
        r = _lm_request_guarded(
            client,
            "POST",
            url,
            json=payload,
            headers=_auth_headers(),
        )
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"].strip()


def llm_complete_vision(
    *,
    image_b64: str,
    context_text: str,
    system_prompt: str,
    max_tokens: int,
) -> str:
    """
    Caller Vision per ``V2VisionEnricher`` — richiede modello VL su LM Studio.

    Env: ``V2_VISION_MODEL`` / ``LM_VISION_MODEL`` (override), altrimenti session model.
    """
    model = _vision_model_id()
    safe_max = min(max_tokens, int(os.environ.get("V2_VISION_MAX_TOKENS", "512")))
    with _llm_semaphore:
        if _backend() == AIBackend.OLLAMA:
            raise RuntimeError(
                "Vision multimodale non supportata su backend Ollama in questa build; "
                "usa AI_BACKEND=lm_studio e un modello VL."
            )
        if _backend() == AIBackend.OPENAI:
            base = os.environ.get("OPENAI_BASE_URL", LM_OPENAI_BASE_URL)
        else:
            base = LM_OPENAI_BASE_URL
        result = _complete_openai_vision(
            base_url=base,
            model=model,
            system_prompt=system_prompt,
            image_b64=image_b64,
            context_text=context_text,
            max_tokens=safe_max,
        )
    from engine.cooldown_manager import get_cooldown_manager

    get_cooldown_manager().after_llm_call(_orchestrator_state().stop_event)
    return result


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def llm_extract_facts(*, text: str, context: str, max_tokens: int) -> str:
    """
    Caller facts per ``V2RollingMemory`` — testo + contesto rolling → JSON strutturato.
    """
    user_message = (
        f"Contesto rolling precedente (può essere vuoto):\n{context.strip()}\n\n"
        f"---\n\nTesto chunk da analizzare:\n{text.strip()}"
    )
    raw = llm_complete(
        system_prompt=V2_FACT_EXTRACT_SYSTEM_PROMPT,
        user_message=user_message,
        temperature=0.05,
        max_tokens=max_tokens,
    )
    return _strip_json_fence(raw)
