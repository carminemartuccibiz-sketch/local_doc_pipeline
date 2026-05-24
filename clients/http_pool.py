"""
Pool HTTP condivisi (audit GPT §1.1) — limiti connessione, timeout granulari.

I client sono ri-creati dopo kill_all() via close_all_http_clients().
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from config import ALLM_TIMEOUT_S, ANYTHINGLLM_API_KEY, LM_TIMEOUT_S

_POOL_LOCK = threading.RLock()
_lm_client: httpx.Client | None = None
_allm_client: httpx.Client | None = None

HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)


def _lm_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=10.0,
        read=float(LM_TIMEOUT_S),
        write=30.0,
        pool=30.0,
    )


def _allm_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=10.0,
        read=float(ALLM_TIMEOUT_S),
        write=30.0,
        pool=30.0,
    )


def _allm_default_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if ANYTHINGLLM_API_KEY:
        headers["Authorization"] = f"Bearer {ANYTHINGLLM_API_KEY}"
    return headers


def get_lm_client(**overrides: Any) -> httpx.Client:
    """Client LM Studio / OpenAI-compatible (singleton thread-safe)."""
    if overrides:
        return httpx.Client(
            timeout=overrides.pop("timeout", _lm_timeout()),
            limits=HTTP_LIMITS,
            **overrides,
        )
    global _lm_client
    with _POOL_LOCK:
        if _lm_client is None or _lm_client.is_closed:
            _lm_client = httpx.Client(timeout=_lm_timeout(), limits=HTTP_LIMITS)
        return _lm_client


def get_allm_client(**overrides: Any) -> httpx.Client:
    """Client AnythingLLM (singleton; header auth da config)."""
    if overrides:
        headers = {**_allm_default_headers(), **(overrides.pop("headers", None) or {})}
        return httpx.Client(
            timeout=overrides.pop("timeout", _allm_timeout()),
            limits=HTTP_LIMITS,
            headers=headers,
            **overrides,
        )
    global _allm_client
    with _POOL_LOCK:
        if _allm_client is None or _allm_client.is_closed:
            _allm_client = httpx.Client(
                timeout=_allm_timeout(),
                limits=HTTP_LIMITS,
                headers=_allm_default_headers(),
            )
        return _allm_client


def close_all_http_clients() -> int:
    """Chiude i pool (kill switch livello 2). Ritorna quanti client chiusi."""
    global _lm_client, _allm_client
    closed = 0
    with _POOL_LOCK:
        for name in ("_lm_client", "_allm_client"):
            client = globals()[name]
            if client is not None:
                try:
                    if not client.is_closed:
                        client.close()
                        closed += 1
                except Exception:
                    pass
                globals()[name] = None
    return closed
