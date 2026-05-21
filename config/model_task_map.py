"""Mapping task type → keyword modello LM Studio (FASE 4)."""
from __future__ import annotations

TASK_KEYWORDS: dict[str, list[str]] = {
    "reasoning": ["qwen", "deepseek", "mistral-nemo", "llama-3"],
    "summary": ["phi", "gemma", "llama-3.2"],
    "fast": ["phi", "smollm", "qwen2.5-3b"],
}
