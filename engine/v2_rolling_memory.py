"""
V2 Stage 4 — Rolling Context Manager.

Pattern: facts-based (non prose summary) per evitare drift cumulativo.
Ogni chunk produce un estratto strutturato; il manager mantiene
una finestra mobile degli ultimi N fatti ad alta confidenza.

Persistenza: ``02_STAGING/<doc_id>/rolling_memory/rolling_state.json``
(separato da map.json per ridurre contention).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from core.file_io import atomic_write_json
from engine.project_memory import V2StagingLayout, setup_v2_staging_dirs
from engine.v2_map_manager import V2MapManager

logger = logging.getLogger(__name__)


class FactExtractorLLM(Protocol):
    """
    Iniettabile — estrae fatti strutturati da testo.

    Deve restituire JSON: ``{"facts": [...], "entities": [...], "decisions": [...]}``
    """

    def __call__(self, *, text: str, context: str, max_tokens: int) -> str: ...


@dataclass
class RollingFact:
    claim: str
    section: str
    confidence: str  # "high" | "medium" | "low"
    chunk_id: str
    entity_refs: list[str] = field(default_factory=list)


@dataclass
class RollingState:
    facts: list[RollingFact] = field(default_factory=list)
    entities: set[str] = field(default_factory=set)
    decisions: list[str] = field(default_factory=list)
    last_chunk_id: str | None = None

    MAX_FACTS: int = 30
    CONFIDENCE_PRIORITY: dict[str, int] = field(
        default_factory=lambda: {"high": 0, "medium": 1, "low": 2},
        repr=False,
    )

    def add_extract(self, extract: dict[str, Any], chunk_id: str) -> None:
        for fact in extract.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            self.facts.append(
                RollingFact(
                    claim=str(fact.get("claim") or ""),
                    section=str(fact.get("section") or ""),
                    confidence=str(fact.get("confidence") or "low"),
                    chunk_id=chunk_id,
                    entity_refs=list(fact.get("entity_refs") or []),
                )
            )
        for entity in extract.get("entities") or []:
            if isinstance(entity, str):
                self.entities.add(entity)
        for decision in extract.get("decisions") or []:
            if isinstance(decision, str) and decision not in self.decisions:
                self.decisions.append(decision)
        self.last_chunk_id = chunk_id
        self._trim()

    def _trim(self) -> None:
        """Mantieni MAX_FACTS: priorità alta > media > bassa, poi FIFO."""
        self.facts.sort(
            key=lambda f: self.CONFIDENCE_PRIORITY.get(f.confidence, 2)
        )
        self.facts = self.facts[: self.MAX_FACTS]

    def build_context_block(self) -> str:
        """Blocco testuale denso da passare al prossimo chunk come contesto."""
        if not self.facts:
            return ""
        entities_str = ", ".join(sorted(self.entities)[:10])
        facts_lines = "\n".join(
            f"• [{f.confidence}] {f.claim} (§{f.section})"
            for f in self.facts[-15:]
        )
        decisions_str = (
            "\nDecisioni: " + " | ".join(self.decisions[-5:])
            if self.decisions
            else ""
        )
        return (
            f"[Rolling Context — entità: {entities_str}]\n"
            f"{facts_lines}{decisions_str}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": [vars(f) for f in self.facts],
            "entities": sorted(self.entities),
            "decisions": self.decisions,
            "last_chunk_id": self.last_chunk_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RollingState:
        state = cls()
        for fd in data.get("facts") or []:
            if not isinstance(fd, dict):
                continue
            state.facts.append(
                RollingFact(
                    claim=str(fd.get("claim") or ""),
                    section=str(fd.get("section") or ""),
                    confidence=str(fd.get("confidence") or "low"),
                    chunk_id=str(fd.get("chunk_id") or ""),
                    entity_refs=list(fd.get("entity_refs") or []),
                )
            )
        state.entities = set(data.get("entities") or [])
        state.decisions = list(data.get("decisions") or [])
        state.last_chunk_id = data.get("last_chunk_id")
        return state


@dataclass
class V2RollingMemory:
    """
    Gestisce il rolling context tra chunk durante l'enrichment.

    Flusso per ogni chunk:
      1. ``build_context_block()`` → passa al LLM come contesto del chunk precedente
      2. LLM processa chunk + context → produce extract
      3. ``add_from_llm_extract(extract, chunk_id)`` → aggiorna state
      4. ``save()`` → persiste in ``rolling_memory/rolling_state.json``
      5. ``map_manager.set_compressed_context(...)`` → aggiorna map.json
    """

    rolling_memory_dir: Path
    map_manager: V2MapManager
    extractor: FactExtractorLLM
    max_tokens: int = 400
    log_fn: Callable[[str], None] = field(default=lambda _m: None)
    _state: RollingState = field(init=False)

    def __post_init__(self) -> None:
        self._state = self._load()

    @classmethod
    def for_project(
        cls,
        slug: str,
        document_id: str,
        extractor: FactExtractorLLM,
        *,
        layout: V2StagingLayout | None = None,
        map_manager: V2MapManager | None = None,
        log_fn: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> V2RollingMemory:
        staging = layout or setup_v2_staging_dirs(slug, document_id)
        mgr = map_manager or V2MapManager(staging.map_json)
        return cls(
            staging.rolling_memory,
            map_manager=mgr,
            extractor=extractor,
            log_fn=log_fn or (lambda _m: None),
            **kwargs,
        )

    def _load(self) -> RollingState:
        state_path = self.rolling_memory_dir / "rolling_state.json"
        if state_path.is_file():
            try:
                raw = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return RollingState.from_dict(raw)
            except (json.JSONDecodeError, OSError, TypeError) as exc:
                logger.warning("rolling_state.json illeggibile: %s", exc)
        return RollingState()

    def save(self) -> None:
        state_path = self.rolling_memory_dir / "rolling_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state_path, self._state.to_dict())

    def process_chunk(
        self,
        chunk_id: str,
        chunk_text: str,
        *,
        stop_event: Any | None = None,
    ) -> str:
        """
        Processa un chunk: estrae fatti, aggiorna state, ritorna context block
        da usare per il chunk SUCCESSIVO.
        """
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("RollingMemory interrotto")

        context = self._state.build_context_block()
        self.log_fn(f"[ROLLING] Chunk {chunk_id}: context {len(context)} chars")

        try:
            raw = self.extractor(
                text=chunk_text,
                context=context,
                max_tokens=self.max_tokens,
            )
            extract = json.loads(raw)
            if not isinstance(extract, dict):
                extract = {}
        except Exception as exc:
            self.log_fn(f"[ROLLING] Estrazione facts fallita per {chunk_id}: {exc}")
            logger.debug("Fact extraction fallita per %s", chunk_id, exc_info=True)
            extract = {}

        self._state.add_extract(extract, chunk_id)
        self.save()

        compressed = self._state.build_context_block()
        self.map_manager.set_last_merged_chunk(chunk_id)
        self.map_manager.set_compressed_context(compressed)
        self.map_manager.set_chunk_knowledge_state(
            chunk_id,
            rolling_context_merged=True,
        )
        self._sync_map_structured(chunk_id, extract)

        return compressed

    def process_all_chunks(
        self,
        layout: V2StagingLayout,
        *,
        stop_event: Any | None = None,
        skip_merged: bool = True,
    ) -> int:
        """Processa tutti i chunk in ordine map (linked list array order)."""
        processed = 0
        for chunk in self.map_manager.list_chunks():
            chunk_id = str(chunk.get("id") or "")
            if not chunk_id:
                continue
            ks = chunk.get("knowledge_state") or {}
            if skip_merged and ks.get("rolling_context_merged"):
                continue

            if stop_event is not None and stop_event.is_set():
                self.log_fn("[ROLLING] Interrotto da stop_event")
                break

            chunk_text = self._read_chunk_text(layout, chunk)
            self.process_chunk(chunk_id, chunk_text, stop_event=stop_event)
            processed += 1

        if processed:
            self.map_manager.set_stage(
                "rolling_memory",
                "completed",
                chunks_processed=processed,
            )
            logger.info(
                "Rolling memory completato: %d chunk in %s",
                processed,
                layout.root,
            )
        return processed

    def _read_chunk_text(self, layout: V2StagingLayout, chunk: dict[str, Any]) -> str:
        rel_path = str(chunk.get("path") or "")
        chunk_path = layout.root / rel_path if rel_path else layout.chunks / f"{chunk['id']}.md"
        parts: list[str] = []
        if chunk_path.is_file():
            parts.append(chunk_path.read_text(encoding="utf-8"))

        enriched_path = layout.enriched / f"{chunk['id']}.enriched.md"
        if enriched_path.is_file():
            parts.append(enriched_path.read_text(encoding="utf-8"))

        return "\n\n".join(parts)

    def _sync_map_structured(self, chunk_id: str, extract: dict[str, Any]) -> None:
        """Merge leggero in map.json structured + temporal marker."""
        facts_payload: list[dict[str, Any]] = []
        for fact in extract.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            facts_payload.append(
                {
                    "text": str(fact.get("claim") or ""),
                    "chunk_id": chunk_id,
                    "confidence": str(fact.get("confidence") or "low"),
                    "section": str(fact.get("section") or ""),
                }
            )
        entities = [e for e in (extract.get("entities") or []) if isinstance(e, str)]
        patch: dict[str, list[Any]] = {}
        if facts_payload:
            patch["facts"] = facts_payload
        if entities:
            patch["entities"] = entities
        decisions = [d for d in (extract.get("decisions") or []) if isinstance(d, str)]
        if decisions:
            patch["decisions"] = decisions
        if patch:
            self.map_manager.merge_rolling_structured(patch)

        self.map_manager.merge_rolling_structured(
            {
                "temporal_markers": [
                    {
                        "chunk_id": chunk_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ]
            }
        )
