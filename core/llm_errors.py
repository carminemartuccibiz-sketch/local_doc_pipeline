"""LLM error classification for fallback routing."""
from __future__ import annotations


class LLMRecoverableError(RuntimeError):
    pass


class LLMFatalError(RuntimeError):
    pass


class ContextOverflowError(LLMRecoverableError):
    pass


class ModelOOMError(LLMRecoverableError):
    pass


def classify_llm_error(exc: Exception) -> Exception:
    msg = str(exc).lower()
    if "out of memory" in msg or "oom" in msg:
        return ModelOOMError(str(exc))
    if "context" in msg and ("exceed" in msg or "length" in msg):
        return ContextOverflowError(str(exc))
    if "timeout" in msg or "timed out" in msg:
        return LLMRecoverableError(str(exc))
    return LLMFatalError(str(exc))
