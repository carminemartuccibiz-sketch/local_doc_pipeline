"""Metadata capability workflow plugin (audit GPT §3.5)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkflowCapabilities:
    requires_llm: bool = True
    requires_rag: bool = False
    supports_cancel: bool = True
