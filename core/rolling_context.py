"""
Facts-based rolling context — anti-drift alternative to prose condensates.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RollingContext:
    max_facts: int = 20

    _facts: list[dict] = field(default_factory=list)
    _headings: list[str] = field(default_factory=list)

    def add_chunk_result(self, extract: dict, heading: str) -> None:
        if heading:
            self._headings.append(heading)
        for f in extract.get("facts", []):
            if not isinstance(f, dict):
                continue
            if f.get("confidence") in ("high", "medium"):
                self._facts.append(f)
        self._facts = self._facts[-self.max_facts :]

    def build_context_block(self) -> str:
        if not self._facts:
            return ""
        headings_str = " → ".join(self._headings[-3:])
        facts_str = "\n".join(
            f"• {f.get('claim', '')} [{f.get('section', '?')}]"
            for f in self._facts[-10:]
        )
        return (
            f"[Contesto da chunk precedenti — {headings_str}]\n{facts_str}"
        )
