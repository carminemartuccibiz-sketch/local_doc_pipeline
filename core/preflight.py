"""
Pre-volo: verifica che LM Studio e AnythingLLM rispondano prima del processing.
"""
from __future__ import annotations

import sys

import httpx

from config import (
    ANYTHINGLLM_API_KEY,
    ANYTHINGLLM_API_V1_URL,
    ANYTHINGLLM_BASE_URL,
    LM_OPENAI_BASE_URL,
    LM_TIMEOUT_S,
)
import os

PREFLIGHT_ERROR_MSG = (
    "ERRORE: Server LLM locali non raggiungibili. "
    "Assicurati di aver avviato il server in LM Studio e AnythingLLM prima di procedere."
)


class LocalAIPreflightError(RuntimeError):
    """Server locali non disponibili per Gap Analysis."""


def ping_lm_studio(*, base_url: str = LM_OPENAI_BASE_URL) -> tuple[bool, str]:
    """GET {base}/models — endpoint OpenAI-compatible LM Studio."""
    url = f"{base_url.rstrip('/')}/models"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url)
            if r.status_code == 200:
                return True, url
            return False, f"{url} -> HTTP {r.status_code}"
    except httpx.RequestError as e:
        return False, f"{url} -> {e}"


def ping_anythingllm(
    *,
    base_url: str = ANYTHINGLLM_BASE_URL,
    api_key: str = ANYTHINGLLM_API_KEY,
) -> tuple[bool, str]:
    """Ping REST AnythingLLM (default /api/ping)."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    ping_url = f"{base_url.rstrip('/')}/api/ping"
    try:
        with httpx.Client(timeout=15.0, headers=headers) as client:
            r = client.get(ping_url)
            if r.status_code == 200:
                return True, ping_url
            if r.status_code == 403:
                return False, f"{ping_url} -> HTTP 403 (API key mancante o non valida)"
            return False, f"{ping_url} -> HTTP {r.status_code}"
    except httpx.RequestError as e:
        return False, f"{ping_url} -> {e}"


def run_preflight_checks(
    *,
    require_lm: bool = True,
    require_allm: bool = True,
    exit_on_failure: bool = False,
) -> None:
    """
    Verifica connettivita locale. Solleva LocalAIPreflightError se un check richiesto fallisce.
    """
    backend = os.environ.get("AI_BACKEND", "lm_studio").strip().lower()
    failures: list[str] = []

    if require_lm and backend in ("lm_studio", "lm-studio", ""):
        ok, detail = ping_lm_studio()
        if not ok:
            failures.append(f"  - LM Studio ({LM_OPENAI_BASE_URL}): {detail}")
        else:
            print(f"  OK LM Studio: {detail}")

    if require_allm:
        if not ANYTHINGLLM_API_KEY:
            failures.append(
                "  - AnythingLLM: ANYTHINGLLM_API_KEY non impostata in .env"
            )
        else:
            ok, detail = ping_anythingllm()
            if not ok:
                failures.append(f"  - AnythingLLM ({ANYTHINGLLM_API_V1_URL}): {detail}")
            else:
                print(f"  OK AnythingLLM: {detail} (API v1: {ANYTHINGLLM_API_V1_URL})")

    if failures:
        print(f"\n{PREFLIGHT_ERROR_MSG}\n")
        for line in failures:
            print(line)
        print(
            "\nAzioni:\n"
            "  1. LM Studio -> Developer -> Start Server (porta 1234)\n"
            "  2. AnythingLLM desktop avviato (porta 3001)\n"
            "  3. Verifica tools/local_doc_pipeline/.env\n"
        )
        if exit_on_failure:
            raise SystemExit(2)
        raise LocalAIPreflightError(PREFLIGHT_ERROR_MSG)
