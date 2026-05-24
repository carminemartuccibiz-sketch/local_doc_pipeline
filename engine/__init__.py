"""Motore orchestrazione desktop — export lazy (no import circolare con clients)."""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "CooldownManager",
    "ModelRouter",
    "OrchestratorState",
    "get_cooldown_manager",
    "get_model_router",
    "get_orchestrator_state",
    "get_state",
    "reset_orchestrator",
    "reset_state",
]

_LAZY: dict[str, tuple[str, str]] = {
    "CooldownManager": ("engine.cooldown_manager", "CooldownManager"),
    "get_cooldown_manager": ("engine.cooldown_manager", "get_cooldown_manager"),
    "ModelRouter": ("engine.model_router", "ModelRouter"),
    "get_model_router": ("engine.model_router", "get_model_router"),
    "OrchestratorState": ("engine.orchestrator", "OrchestratorState"),
    "get_orchestrator_state": ("engine.orchestrator", "get_orchestrator_state"),
    "get_state": ("engine.orchestrator", "get_state"),
    "reset_orchestrator": ("engine.orchestrator", "reset_orchestrator"),
    "reset_state": ("engine.orchestrator", "reset_state"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_path, attr = _LAZY[name]
    value = getattr(importlib.import_module(mod_path), attr)
    globals()[name] = value
    return value
