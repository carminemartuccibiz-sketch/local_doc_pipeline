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
    raw_budget = min(
        DEFAULT_RAW_INPUT_TOKEN_BUDGET,
        int(ctx * 0.45),
    )
    response_reserve = max(1024, int(ctx * 0.2))
    sot_budget = max(1500, ctx - raw_budget - response_reserve)
    return TokenLimits(
        model_id=model_id,
        context_tokens=ctx,
        raw_input_budget=raw_budget,
        sot_budget_tokens=sot_budget,
        response_reserve=response_reserve,
    )


def raw_budget_to_chars(token_budget: int) -> int:
    """Conversione conservativa per build_context_bundle legacy."""
    return token_budget * _CHARS_PER_TOKEN_FALLBACK
