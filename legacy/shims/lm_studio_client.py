"""Shim compatibilità — implementazione in clients.lm_studio."""
from clients.lm_studio import (
    EXTRACTOR_SYSTEM_PROMPT,
    LMStudioClient,
    LMStudioError,
    build_user_prompt,
    split_text,
)

__all__ = [
    "EXTRACTOR_SYSTEM_PROMPT",
    "LMStudioClient",
    "LMStudioError",
    "build_user_prompt",
    "split_text",
]
