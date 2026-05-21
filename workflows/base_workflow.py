"""ABC workflow — process_file(file, ctx) -> Result (FASE 2+)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseWorkflow(ABC):
    @abstractmethod
    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> Any:
        """Elabora un singolo file nel contesto del progetto attivo."""
