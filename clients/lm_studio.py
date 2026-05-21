"""
Client LM Studio — OpenAI-compatible + fallback native /api/v1/chat.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from clients.http_helpers import lm_request
from config import (
    LM_API_KEY,
    LM_MODEL,
    LM_NATIVE_CHAT_URL,
    LM_OPENAI_BASE_URL,
    LM_TIMEOUT_S,
    LM_USE_NATIVE_CHAT,
)

logger = logging.getLogger(__name__)

EXTRACTOR_SYSTEM_PROMPT = """Agisci come un estrattore di dati estremamente pignolo per il progetto
DVAMOCLES SWORD™: Material Forge Studio® e SIGNUM SENTINEL.

Il tuo compito è leggere i frammenti di documentazione forniti e generare un NUOVO documento Markdown.

REGOLE OBBLIGATORIE:
1. DEVI elencare assolutamente OGNI dettaglio specifico: logiche architetturali, flussi di lavoro,
   nomenclature, moduli pipeline, regole filesystem, vincoli PBR, UI/UX, parametri numerici, API, JSON schema.
2. NON riassumere. NON omettere per brevità. L'eccesso di dettaglio è richiesto.
3. Se trovi informazioni in contrasto o ridondanti, riportale ENTRAMBE con sezione
   "## Conflitti / versioni multiple" annotando la fonte di ciascuna variante.
4. Struttura l'output con titoli ## e ###, elenchi puntati, tabelle quando utile.
5. In coda aggiungi "## Metadati estrazione" con: file sorgente, eventuali gap, domande aperte.
6. Non inventare feature non presenti nei frammenti; se manca un dato scrivi "_non specificato nei chunk_".

Questo documento alimenterà un refactor documentale successivo (Claude): la completezza batte la leggibilità."""


class LMStudioError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if LM_API_KEY:
        h["Authorization"] = f"Bearer {LM_API_KEY}"
    return h


def _http_request(
    method: str,
    url: str,
    *,
    timeout: float = LM_TIMEOUT_S,
    **kwargs: Any,
) -> httpx.Response:
    """Wrap HTTP verso LM Studio con interaction log (rolling 5)."""
    with httpx.Client(timeout=timeout, headers=_headers()) as client:
        return lm_request(client, method, url, **kwargs)


def _parse_native_response(data: dict[str, Any]) -> str:
    from core.ai_tasks import parse_lm_native_response

    return parse_lm_native_response(data)


class LMStudioClient:
    def __init__(
        self,
        model: str | None = None,
        use_native: bool = LM_USE_NATIVE_CHAT,
    ) -> None:
        if model:
            self.model = model
        elif LM_MODEL and LM_MODEL.lower() not in ("auto", "discover"):
            self.model = LM_MODEL
        else:
            from core.ai_tasks import discover_lm_studio_model

            self.model = discover_lm_studio_model()
        self.use_native = use_native

    def health(self) -> bool:
        try:
            r = _http_request(
                "GET",
                f"{LM_OPENAI_BASE_URL.rstrip('/')}/models",
                timeout=15.0,
            )
            return r.status_code == 200
        except httpx.RequestError:
            return False

    def list_models(self) -> list[str]:
        try:
            r = _http_request(
                "GET",
                f"{LM_OPENAI_BASE_URL.rstrip('/')}/models",
                timeout=15.0,
            )
            r.raise_for_status()
            data = r.json()
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except Exception:
            return []

    def complete(
        self,
        *,
        user_message: str,
        system_prompt: str = EXTRACTOR_SYSTEM_PROMPT,
        temperature: float = 0.15,
        max_tokens: int = 8192,
    ) -> str:
        from core.ai_tasks import llm_complete

        try:
            return llm_complete(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except RuntimeError as e:
            raise LMStudioError(str(e)) from e
        except httpx.HTTPError as e:
            raise LMStudioError(f"LM Studio request failed: {e}") from e


def build_user_prompt(
    *,
    rel_path: str,
    local_excerpt: str,
    rag_chunks: list[dict[str, Any]],
    part_label: str | None = None,
) -> str:
    rag_block = []
    for i, ch in enumerate(rag_chunks, 1):
        meta = ch.get("metadata") or {}
        title = meta.get("title") or meta.get("chunkSource") or "chunk"
        text = ch.get("text") or ""
        score = ch.get("score")
        rag_block.append(
            f"### Chunk RAG {i} (score={score})\n**{title}**\n\n{text}\n"
        )
    part_hdr = f"\n**Parte documento:** {part_label}\n" if part_label else ""
    return f"""# Documento da estrarre
**Percorso sorgente:** `{rel_path}`{part_hdr}

## Testo normalizzato (conversione locale)
{local_excerpt}

## Chunk correlati dal workspace RAG (AnythingLLM)
{"".join(rag_block) if rag_block else "_Nessun chunk RAG — usa solo il testo normalizzato._"}

Genera il Markdown di estrazione completo seguendo il system prompt.
"""


def split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            break_at = text.rfind("\n\n", start, end)
            if break_at > start + max_chars // 2:
                end = break_at
        parts.append(text[start:end])
        start = end
    return parts
