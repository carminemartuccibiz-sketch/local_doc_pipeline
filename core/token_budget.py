"""
Token management: tiktoken + euristica, limiti per modello LM Studio, chunking input.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN_FALLBACK = 4

# Budget token documento grezzo (resto per SOT + risposta)
DEFAULT_RAW_INPUT_TOKEN_BUDGET = int(os.environ.get("GAP_RAW_INPUT_TOKEN_BUDGET", "6000"))
DEFAULT_MODEL_CONTEXT = int(os.environ.get("GAP_MODEL_CONTEXT_TOKENS", "8192"))
# Chunk LLM: più piccolo del budget contesto → analisi per sezione, meno troncamenti
DEFAULT_GAP_CHUNK_MAX_TOKENS = int(os.environ.get("GAP_CHUNK_MAX_TOKENS", "1500"))


@dataclass(slots=True)
class TokenLimits:
    model_id: str
    context_tokens: int
    raw_input_budget: int
    sot_budget_tokens: int
    response_reserve: int


def count_tokens(text: str, *, model_hint: str = "cl100k_base") -> int:
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model_hint)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, len(text) // _CHARS_PER_TOKEN_FALLBACK)


def infer_context_window(model_id: str) -> int:
    low = model_id.lower()
    for pattern, ctx in (
        (r"128k|131072", 131072),
        (r"32k|32768", 32768),
        (r"16k|16384", 16384),
        (r"8k|8192", 8192),
        (r"4k|4096", 4096),
    ):
        if re.search(pattern, low):
            return ctx
    return DEFAULT_MODEL_CONTEXT


def resolve_token_limits(model_id: str) -> TokenLimits:
    ctx = infer_context_window(model_id)
    loaded_cap = int(os.environ.get("LM_NATIVE_CONTEXT", "0") or "0")
    if loaded_cap > 0:
        ctx = min(ctx, loaded_cap)

    # ~35% grezzo, ~20% output, resto system/RAG/overhead (LM Studio loaded_context)
    raw_budget = min(
        DEFAULT_RAW_INPUT_TOKEN_BUDGET,
        int(ctx * 0.35),
    )
    response_reserve = max(
        1024,
        min(int(os.environ.get("GAP_LM_MAX_OUTPUT", "1500")), int(ctx * 0.18)),
    )
    sot_budget = max(800, ctx - raw_budget - response_reserve - 800)
    return TokenLimits(
        model_id=model_id,
        context_tokens=ctx,
        raw_input_budget=raw_budget,
        sot_budget_tokens=sot_budget,
        response_reserve=response_reserve,
    )


def resolve_chunk_max_tokens(limits: TokenLimits) -> int:
    """
    Dimensione massima di ogni chunk inviato al modello (split markdown).
    Separato da raw_input_budget: evita un solo chunk «full» su file medi.
    """
    cap = int(os.environ.get("GAP_CHUNK_MAX_TOKENS", str(DEFAULT_GAP_CHUNK_MAX_TOKENS)))
    cap = max(400, min(cap, limits.raw_input_budget))
    return cap


def resolve_chunk_min_section_tokens() -> int:
    return max(100, int(os.environ.get("GAP_CHUNK_MIN_SECTION_TOKENS", "250")))


def raw_budget_to_chars(token_budget: int) -> int:
    """Conversione conservativa per build_context_bundle legacy."""
    return token_budget * _CHARS_PER_TOKEN_FALLBACK


def validate_request_budget(
    *,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    memory: str = "",
    rag: str = "",
    reserved_output: int = 2048,
) -> dict[str, int | bool]:
    """Preflight: stima token totali vs contesto utilizzabile (~80% context - output)."""
    limits = resolve_token_limits(model_id)
    usable = int(limits.context_tokens * 0.80) - reserved_output
    projected = (
        count_tokens(system_prompt, model_hint=model_id)
        + count_tokens(user_prompt, model_hint=model_id)
        + count_tokens(memory, model_hint=model_id)
        + count_tokens(rag, model_hint=model_id)
        + reserved_output
    )
    overflow = max(0, projected - usable)
    return {
        "fits": projected < usable,
        "projected": projected,
        "usable": usable,
        "overflow": overflow,
    }
