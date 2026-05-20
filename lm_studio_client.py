"""
Client LM Studio — OpenAI-compatible + fallback native /api/v1/chat.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

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


def _parse_native_response(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in data.get("output") or []:
        if isinstance(block, dict) and block.get("type") == "message":
            parts.append(str(block.get("content") or ""))
    return "\n".join(parts).strip()


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
            with httpx.Client(timeout=15.0, headers=_headers()) as c:
                r = c.get(f"{LM_OPENAI_BASE_URL}/models")
                return r.status_code == 200
        except httpx.RequestError:
            return False

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=15.0, headers=_headers()) as c:
                r = c.get(f"{LM_OPENAI_BASE_URL}/models")
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
        if self.use_native:
            return self._complete_native(
                user_message=user_message,
                system_prompt=system_prompt,
                temperature=temperature,
            )
        return self._complete_openai(
            user_message=user_message,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _complete_openai(
        self,
        *,
        user_message: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{LM_OPENAI_BASE_URL}/chat/completions"
        with httpx.Client(timeout=LM_TIMEOUT_S, headers=_headers()) as c:
            try:
                r = c.post(url, json=payload)
            except httpx.RequestError as e:
                raise LMStudioError(
                    f"LM Studio non raggiungibile su {url}. Avvia server in LM Studio. ({e})"
                ) from e
            if r.status_code >= 400:
                # fallback native se completions non supportato
                logger.warning("OpenAI completions HTTP %s — retry native chat", r.status_code)
                return self._complete_native(
                    user_message=user_message,
                    system_prompt=system_prompt,
                    temperature=temperature,
                )
            data = r.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise LMStudioError(f"Risposta LM Studio inattesa: {data!r}") from e

    def _complete_native(
        self,
        *,
        user_message: str,
        system_prompt: str,
        temperature: float,
    ) -> str:
        payload = {
            "model": self.model,
            "system_prompt": system_prompt,
            "input": user_message,
            "temperature": temperature,
            "context_length": 32768,
        }
        with httpx.Client(timeout=LM_TIMEOUT_S, headers=_headers()) as c:
            try:
                r = c.post(LM_NATIVE_CHAT_URL, json=payload)
            except httpx.RequestError as e:
                raise LMStudioError(f"Native chat non raggiungibile: {e}") from e
            if r.status_code >= 400:
                raise LMStudioError(f"Native chat HTTP {r.status_code}: {r.text[:400]}")
            return _parse_native_response(r.json())


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
