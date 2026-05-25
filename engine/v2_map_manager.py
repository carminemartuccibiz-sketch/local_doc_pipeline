"""
V2 map.json — semantic operating map per documento staging.

Schema: docs/guides/V2_MULTIMODAL_INGESTION_ARCHITECTURE.md §4–§5.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from core.file_io import atomic_write_json

logger = logging.getLogger(__name__)

PIPELINE_VERSION = "2.0"
MAP_SCHEMA_VERSION = 1

# Lock conmotion per path map.json (condivisi tra istanze / worker).
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}
_CHUNK_LOCKS: dict[tuple[str, str], threading.RLock] = {}

KNOWLEDGE_STATE_DEFAULT: dict[str, bool] = {
    "facts_extracted": False,
    "vision_complete": False,
    "conflict_checked": False,
    "rolling_context_merged": False,
}

ROLLING_MEMORY_STRUCTURE_DEFAULT: dict[str, list[Any]] = {
    "entities": [],
    "facts": [],
    "constraints": [],
    "decisions": [],
    "open_questions": [],
    "vision_insights": [],
    "temporal_markers": [],
}

ROLLING_MEMORY_PATHS_DEFAULT: dict[str, str] = {
    "state_path": "rolling_memory/rolling_state.json",
    "facts_path": "rolling_memory/rolling_facts.json",
    "entity_graph_path": "rolling_memory/entity_graph.json",
}


def empty_map_template(
    *,
    document_id: str = "",
    source_file: str = "",
    document_type: str = "unknown",
) -> dict[str, Any]:
    """Schema canonico map.json (§4 V2 architecture)."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "document_id": document_id,
        "source_file": source_file,
        "document_type": document_type,
        "ingestion_timestamp": now,
        "pipeline_version": PIPELINE_VERSION,
        "map_schema_version": MAP_SCHEMA_VERSION,
        "metadata": {
            "title": "",
            "author": "",
            "language": "",
            "page_count": 0,
            "ocr_used": False,
            "vision_enriched": False,
        },
        "physical_assets": {
            "images": [],
            "tables": [],
        },
        "chunks": [],
        "rolling_memory": {
            **ROLLING_MEMORY_PATHS_DEFAULT,
            "last_merged_chunk_id": None,
            "structured": copy.deepcopy(ROLLING_MEMORY_STRUCTURE_DEFAULT),
            "compressed_context": "",
            "updated_at": None,
        },
        "execution_rules": [],
        "conflicts": [],
        "stages": {},
    }


def normalize_map_data(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Migra map.json legacy (stub Prompt 1) verso schema §4.
    Preserva campi extra non documentati.
    """
    data = copy.deepcopy(raw)
    doc_id = str(data.get("document_id") or data.get("id") or "")

    if data.get("created_at") and not data.get("ingestion_timestamp"):
        data["ingestion_timestamp"] = data["created_at"]

    template = empty_map_template(document_id=doc_id)

    for key, default in template.items():
        if key not in data:
            data[key] = copy.deepcopy(default)

    data["pipeline_version"] = str(data.get("pipeline_version") or PIPELINE_VERSION)
    data["map_schema_version"] = int(data.get("map_schema_version") or MAP_SCHEMA_VERSION)

    meta = data.setdefault("metadata", {})
    if not isinstance(meta, dict):
        meta = {}
        data["metadata"] = meta
    for mk, mv in template["metadata"].items():
        meta.setdefault(mk, mv)

    assets = data.setdefault("physical_assets", {})
    if not isinstance(assets, dict):
        assets = {"images": [], "tables": []}
        data["physical_assets"] = assets
    assets.setdefault("images", [])
    assets.setdefault("tables", [])
    for tbl in assets.get("tables", []):
        if isinstance(tbl, dict):
            _normalize_table(tbl)

    if not isinstance(data.get("chunks"), list):
        data["chunks"] = []

    rolling = data.setdefault("rolling_memory", {})
    if not isinstance(rolling, dict):
        rolling = copy.deepcopy(template["rolling_memory"])
        data["rolling_memory"] = rolling
    for rk, rv in template["rolling_memory"].items():
        if rk == "structured":
            structured = rolling.setdefault("structured", {})
            if not isinstance(structured, dict):
                structured = copy.deepcopy(ROLLING_MEMORY_STRUCTURE_DEFAULT)
                rolling["structured"] = structured
            for sk, sv in ROLLING_MEMORY_STRUCTURE_DEFAULT.items():
                structured.setdefault(sk, copy.deepcopy(sv))
        else:
            rolling.setdefault(rk, copy.deepcopy(rv))

    if not isinstance(data.get("execution_rules"), list):
        data["execution_rules"] = []
    if not isinstance(data.get("conflicts"), list):
        data["conflicts"] = []
    if not isinstance(data.get("stages"), dict):
        data["stages"] = {}

    for chunk in data["chunks"]:
        _normalize_chunk(chunk)

    return data


def _normalize_table(table: dict[str, Any]) -> dict[str, Any]:
    table.setdefault("linked_chunks", [])
    table.setdefault("extraction_confidence", None)
    return table


def _normalize_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    ks = chunk.setdefault("knowledge_state", {})
    if not isinstance(ks, dict):
        ks = copy.deepcopy(KNOWLEDGE_STATE_DEFAULT)
        chunk["knowledge_state"] = ks
    for flag, default in KNOWLEDGE_STATE_DEFAULT.items():
        ks.setdefault(flag, default)
    chunk.setdefault("assets", [])
    chunk.setdefault("semantic_tags", [])
    chunk.setdefault("entities", [])
    chunk.setdefault("pages", [])
    return chunk


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _map_lock_path(map_path: Path) -> Path:
    return map_path.with_suffix(map_path.suffix + ".lock")


@contextmanager
def _os_file_lock(map_path: Path):
    """Cross-process lock portable (``filelock`` — no fcntl/msvcrt)."""
    lock_path = _map_lock_path(map_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=30):
        yield


def _parse_map_json(map_path: Path) -> dict[str, Any]:
    raw = json.loads(map_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"map.json non è un oggetto JSON: {map_path}")
    return normalize_map_data(raw)


def _path_key(path: Path) -> str:
    return str(path.resolve())


def _shared_path_lock(path: Path) -> threading.RLock:
    key = _path_key(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _shared_chunk_lock(path: Path, chunk_id: str) -> threading.RLock:
    key = (_path_key(path), chunk_id)
    with _PATH_LOCKS_GUARD:
        lock = _CHUNK_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CHUNK_LOCKS[key] = lock
        return lock





class V2MapManager:
    """
    CRUD thread-safe su map.json.

    Lock globali (ordine di acquisizione obbligatorio — evita deadlock):
      1. ``_chunk_locks[id]`` (update chunk): acquisito per primo.
      2. ``_file_lock`` (RLock per path): serializza thread in-process.
      3. ``_os_file_lock`` (``filelock``): solo in scrittura cross-process.

    Rilettura in-process sotto ``_file_lock`` non usa ``_os_file_lock``.
    Metodi ``a*`` usano ``asyncio.Lock`` + ``to_thread`` per integrazione async.
    """

    def __init__(
        self,
        map_path: Path | str,
        *,
        auto_create: bool = False,
        document_id: str = "",
        source_file: str = "",
    ) -> None:
        self._path = Path(map_path)
        self._file_lock = _shared_path_lock(self._path)
        self._data = self._load(
            auto_create=auto_create,
            document_id=document_id,
            source_file=source_file,
        )

    @property
    def path(self) -> Path:
        return self._path


    def _chunk_lock(self, chunk_id: str) -> threading.RLock:
        return _shared_chunk_lock(self._path, chunk_id)

    def _read_map_from_disk(self) -> dict[str, Any]:
        """Lettura cross-process (``filelock``) — init / accesso senza ``_file_lock``."""
        with _os_file_lock(self._path):
            return _parse_map_json(self._path)

    def _read_map_inprocess(self) -> dict[str, Any]:
        """Lettura in-process — chiamante deve tenere ``_file_lock`` (no ``filelock``)."""
        return _parse_map_json(self._path)

    def _write_map_to_disk(self) -> None:
        with _os_file_lock(self._path):
            atomic_write_json(self._path, self._data)

    def _load(
        self,
        *,
        auto_create: bool,
        document_id: str,
        source_file: str,
    ) -> dict[str, Any]:
        with self._file_lock:
            if self._path.is_file():
                return self._read_map_inprocess()

            if not auto_create:
                raise FileNotFoundError(f"map.json mancante: {self._path}")

            doc_id = document_id or self._path.parent.name
            data = empty_map_template(
                document_id=doc_id,
                source_file=source_file,
            )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._data = data
            self._write_map_to_disk()
            return data

    def reload(self) -> dict[str, Any]:
        """Ricarica da disco (es. dopo modifica esterna)."""
        with self._file_lock:
            self._data = self._read_map_inprocess()
            return copy.deepcopy(self._data)

    def save(self) -> None:
        """Persistenza atomica su disco."""
        with self._file_lock:
            self._write_map_to_disk()

    def to_dict(self) -> dict[str, Any]:
        with self._file_lock:
            return copy.deepcopy(self._data)

    # --- Document root ---

    def get_document_id(self) -> str:
        return str(self._data.get("document_id") or "")

    def update_document(
        self,
        *,
        source_file: str | None = None,
        document_type: str | None = None,
        ingestion_timestamp: str | None = None,
    ) -> dict[str, Any]:
        with self._file_lock:
            if source_file is not None:
                self._data["source_file"] = source_file
            if document_type is not None:
                self._data["document_type"] = document_type
            if ingestion_timestamp is not None:
                self._data["ingestion_timestamp"] = ingestion_timestamp
            self.save()
            return copy.deepcopy(self._data)

    def set_stage(self, stage: str, status: str, **extra: Any) -> None:
        with self._file_lock:
            stages = self._data.setdefault("stages", {})
            entry = {"status": status, "updated_at": _utc_now_iso()}
            entry.update(extra)
            stages[stage] = entry
            self.save()

    # --- metadata ---

    def get_metadata(self) -> dict[str, Any]:
        with self._file_lock:
            return copy.deepcopy(self._data.get("metadata") or {})

    def update_metadata(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._file_lock:
            meta = self._data.setdefault("metadata", {})
            meta.update(patch)
            self.save()
            return copy.deepcopy(meta)

    # --- physical_assets: images ---

    def list_images(self) -> list[dict[str, Any]]:
        with self._file_lock:
            return copy.deepcopy(self._data["physical_assets"]["images"])

    def get_image(self, image_id: str) -> dict[str, Any] | None:
        with self._file_lock:
            for img in self._data["physical_assets"]["images"]:
                if img.get("id") == image_id:
                    return copy.deepcopy(img)
            return None

    def add_image(self, image: dict[str, Any]) -> dict[str, Any]:
        image_id = str(image.get("id") or "").strip()
        if not image_id:
            raise ValueError("physical_assets.images richiede campo 'id'")
        with self._file_lock:
            images: list[dict[str, Any]] = self._data["physical_assets"]["images"]
            if any(i.get("id") == image_id for i in images):
                raise KeyError(f"Immagine già registrata: {image_id}")
            entry = {
                "id": image_id,
                "path": str(image.get("path") or ""),
                "page": image.get("page"),
                "hash": str(image.get("hash") or ""),
                "vision_processed": bool(image.get("vision_processed", False)),
                "linked_chunks": list(image.get("linked_chunks") or []),
            }
            images.append(entry)
            self.save()
            return copy.deepcopy(entry)

    def update_image(self, image_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._file_lock:
            for img in self._data["physical_assets"]["images"]:
                if img.get("id") == image_id:
                    img.update(patch)
                    self.save()
                    return copy.deepcopy(img)
            raise KeyError(f"Immagine non trovata: {image_id}")

    def remove_image(self, image_id: str) -> None:
        with self._file_lock:
            images = self._data["physical_assets"]["images"]
            self._data["physical_assets"]["images"] = [
                i for i in images if i.get("id") != image_id
            ]
            self.save()

    # --- physical_assets: tables ---

    def list_tables(self) -> list[dict[str, Any]]:
        with self._file_lock:
            return copy.deepcopy(self._data["physical_assets"]["tables"])

    def get_table(self, table_id: str) -> dict[str, Any] | None:
        with self._file_lock:
            for tbl in self._data["physical_assets"]["tables"]:
                if tbl.get("id") == table_id:
                    return copy.deepcopy(tbl)
            return None

    def add_table(self, table: dict[str, Any]) -> dict[str, Any]:
        table_id = str(table.get("id") or "").strip()
        if not table_id:
            raise ValueError("physical_assets.tables richiede campo 'id'")
        with self._file_lock:
            tables: list[dict[str, Any]] = self._data["physical_assets"]["tables"]
            if any(t.get("id") == table_id for t in tables):
                raise KeyError(f"Tabella già registrata: {table_id}")
            entry = {
                "id": table_id,
                "path": str(table.get("path") or ""),
                "page": table.get("page"),
                "linked_chunks": list(table.get("linked_chunks") or []),
                "extraction_confidence": table.get("extraction_confidence"),
            }
            _normalize_table(entry)
            tables.append(entry)
            self.save()
            return copy.deepcopy(entry)

    def update_table(self, table_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._file_lock:
            for tbl in self._data["physical_assets"]["tables"]:
                if tbl.get("id") == table_id:
                    tbl.update(patch)
                    self.save()
                    return copy.deepcopy(tbl)
            raise KeyError(f"Tabella non trovata: {table_id}")

    def remove_table(self, table_id: str) -> None:
        with self._file_lock:
            tables = self._data["physical_assets"]["tables"]
            self._data["physical_assets"]["tables"] = [
                t for t in tables if t.get("id") != table_id
            ]
            self.save()

    # --- chunks ---

    def _find_chunk_index(self, chunk_id: str) -> int:
        for idx, chunk in enumerate(self._data["chunks"]):
            if chunk.get("id") == chunk_id:
                return idx
        raise KeyError(f"Chunk non trovato: {chunk_id}")

    def list_chunks(self) -> list[dict[str, Any]]:
        with self._file_lock:
            return copy.deepcopy(self._data["chunks"])

    def _rebuild_chunk_links_unlocked(self) -> None:
        """Ricalcola previous/next su tutti i chunk in ordine array (idempotente)."""
        chunks = self._data.get("chunks", [])
        for i, chunk in enumerate(chunks):
            chunk["previous_chunk"] = chunks[i - 1]["id"] if i > 0 else None
            chunk["next_chunk"] = (
                chunks[i + 1]["id"] if i < len(chunks) - 1 else None
            )

    def rebuild_chunk_links(self) -> None:
        """Ricalcola linked list chunk — invocabile in recovery dopo retry parziali."""
        with self._file_lock:
            self._rebuild_chunk_links_unlocked()
            self._write_map_to_disk()

    def set_chunks(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Sostituisce l'array ``chunks`` (passo physical chunking V2).
        Collega ``previous_chunk`` / ``next_chunk`` via ``rebuild_chunk_links`` (idempotente).
        """
        with self._file_lock:
            normalized: list[dict[str, Any]] = []
            for chunk in chunks:
                chunk_id = str(chunk.get("id") or "").strip()
                if not chunk_id:
                    raise ValueError("ogni chunk richiede 'id'")
                normalized.append(
                    _normalize_chunk(
                        {
                            "id": chunk_id,
                            "path": str(chunk.get("path") or f"chunks/{chunk_id}.md"),
                            "enriched_path": str(
                                chunk.get("enriched_path")
                                or f"enriched/{chunk_id}.enriched.md"
                            ),
                            "section": str(chunk.get("section") or ""),
                            "pages": list(chunk.get("pages") or []),
                            "char_count": int(chunk.get("char_count") or 0),
                            "token_estimate": int(chunk.get("token_estimate") or 0),
                            "previous_chunk": chunk.get("previous_chunk"),
                            "next_chunk": chunk.get("next_chunk"),
                            "rolling_context_ref": chunk.get("rolling_context_ref"),
                            "assets": list(chunk.get("assets") or []),
                            "semantic_tags": list(chunk.get("semantic_tags") or []),
                            "entities": list(chunk.get("entities") or []),
                            "knowledge_state": dict(
                                chunk.get("knowledge_state") or KNOWLEDGE_STATE_DEFAULT
                            ),
                        }
                    )
                )

            self._data["chunks"] = normalized
            self._rebuild_chunk_links_unlocked()
            self.save()
            return copy.deepcopy(normalized)

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        with self._file_lock:
            for chunk in self._data["chunks"]:
                if chunk.get("id") == chunk_id:
                    return copy.deepcopy(chunk)
            return None

    def add_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        chunk_id = str(chunk.get("id") or "").strip()
        if not chunk_id:
            raise ValueError("chunks richiede campo 'id'")
        with self._file_lock:
            if any(c.get("id") == chunk_id for c in self._data["chunks"]):
                raise KeyError(f"Chunk già registrato: {chunk_id}")
            entry = _normalize_chunk(
                {
                    "id": chunk_id,
                    "path": str(chunk.get("path") or f"chunks/{chunk_id}.md"),
                    "enriched_path": str(
                        chunk.get("enriched_path")
                        or f"enriched/{chunk_id}.enriched.md"
                    ),
                    "section": str(chunk.get("section") or ""),
                    "pages": list(chunk.get("pages") or []),
                    "char_count": int(chunk.get("char_count") or 0),
                    "token_estimate": int(chunk.get("token_estimate") or 0),
                    "previous_chunk": chunk.get("previous_chunk"),
                    "next_chunk": chunk.get("next_chunk"),
                    "rolling_context_ref": chunk.get("rolling_context_ref"),
                    "assets": list(chunk.get("assets") or []),
                    "semantic_tags": list(chunk.get("semantic_tags") or []),
                    "entities": list(chunk.get("entities") or []),
                    "knowledge_state": dict(
                        chunk.get("knowledge_state") or KNOWLEDGE_STATE_DEFAULT
                    ),
                }
            )
            self._data["chunks"].append(entry)
            self.save()
            return copy.deepcopy(entry)

    def update_chunk(self, chunk_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """
        Update chunk — reload in-process (``_file_lock``) + write cross-process.

        ``_read_map_inprocess`` evita ``filelock`` in lettura: ``_file_lock``
        serializza già i thread; ``filelock`` resta solo su ``_write_map_to_disk``.
        """
        with self._chunk_lock(chunk_id):
            with self._file_lock:
                self._data = self._read_map_inprocess()
                idx = self._find_chunk_index(chunk_id)
                target = self._data["chunks"][idx]
                target.update(patch)
                _normalize_chunk(target)
                self._write_map_to_disk()
                return copy.deepcopy(target)

    def remove_chunk(self, chunk_id: str) -> None:
        with self._file_lock:
            self._data["chunks"] = [
                c for c in self._data["chunks"] if c.get("id") != chunk_id
            ]
            self.save()

    def link_chunk_assets(self, chunk_id: str, asset_ids: list[str]) -> dict[str, Any]:
        return self.update_chunk(chunk_id, {"assets": list(asset_ids)})

    def set_chunk_knowledge_state(
        self,
        chunk_id: str,
        **flags: bool,
    ) -> dict[str, Any]:
        chunk = self.get_chunk(chunk_id)
        if chunk is None:
            raise KeyError(chunk_id)
        ks = dict(chunk.get("knowledge_state") or {})
        ks.update(flags)
        return self.update_chunk(chunk_id, {"knowledge_state": ks})

    # --- rolling_memory (§5) ---

    def get_rolling_memory(self) -> dict[str, Any]:
        with self._file_lock:
            return copy.deepcopy(self._data.get("rolling_memory") or {})

    def update_rolling_memory(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._file_lock:
            rolling = self._data.setdefault("rolling_memory", {})
            rolling.update(patch)
            rolling["updated_at"] = _utc_now_iso()
            self.save()
            return copy.deepcopy(rolling)

    def merge_rolling_structured(self, patch: dict[str, list[Any]]) -> dict[str, Any]:
        """Merge liste structured (entities, facts, …) senza duplicati per uguaglianza JSON."""
        with self._file_lock:
            rolling = self._data.setdefault("rolling_memory", {})
            structured = rolling.setdefault(
                "structured",
                copy.deepcopy(ROLLING_MEMORY_STRUCTURE_DEFAULT),
            )
            for key, items in patch.items():
                if key not in ROLLING_MEMORY_STRUCTURE_DEFAULT:
                    continue
                bucket = structured.setdefault(key, [])
                for item in items:
                    if item not in bucket:
                        bucket.append(item)
            rolling["updated_at"] = _utc_now_iso()
            self.save()
            return copy.deepcopy(rolling)

    def set_last_merged_chunk(self, chunk_id: str) -> dict[str, Any]:
        return self.update_rolling_memory({"last_merged_chunk_id": chunk_id})

    def set_compressed_context(self, text: str) -> dict[str, Any]:
        return self.update_rolling_memory({"compressed_context": text})

    # --- execution_rules ---

    def list_execution_rules(self) -> list[dict[str, Any]]:
        with self._file_lock:
            return copy.deepcopy(self._data.get("execution_rules") or [])

    def add_execution_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        rule_id = str(rule.get("rule_id") or "").strip()
        if not rule_id:
            raise ValueError("execution_rules richiede 'rule_id'")
        with self._file_lock:
            rules: list[dict[str, Any]] = self._data.setdefault("execution_rules", [])
            if any(r.get("rule_id") == rule_id for r in rules):
                raise KeyError(f"Regola già presente: {rule_id}")
            entry = {
                "rule_id": rule_id,
                "type": str(rule.get("type") or ""),
                "target": str(rule.get("target") or ""),
                "action": str(rule.get("action") or ""),
            }
            rules.append(entry)
            self.save()
            return copy.deepcopy(entry)

    def remove_execution_rule(self, rule_id: str) -> None:
        with self._file_lock:
            rules = self._data.setdefault("execution_rules", [])
            self._data["execution_rules"] = [
                r for r in rules if r.get("rule_id") != rule_id
            ]
            self.save()

    # --- conflicts (map-level registry) ---

    def list_conflicts(self) -> list[dict[str, Any]]:
        with self._file_lock:
            return copy.deepcopy(self._data.get("conflicts") or [])

    def add_conflict(self, conflict: dict[str, Any]) -> dict[str, Any]:
        conflict_id = str(conflict.get("conflict_id") or "").strip()
        if not conflict_id:
            raise ValueError("conflicts richiede 'conflict_id'")
        with self._file_lock:
            items: list[dict[str, Any]] = self._data.setdefault("conflicts", [])
            if any(c.get("conflict_id") == conflict_id for c in items):
                raise KeyError(f"Conflitto già registrato: {conflict_id}")
            entry = {
                "conflict_id": conflict_id,
                "status": str(conflict.get("status") or "pending_human_review"),
                "chunks": list(conflict.get("chunks") or []),
                "reason": str(conflict.get("reason") or ""),
            }
            items.append(entry)
            self.save()
            return copy.deepcopy(entry)

    def update_conflict(self, conflict_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._file_lock:
            for item in self._data.setdefault("conflicts", []):
                if item.get("conflict_id") == conflict_id:
                    item.update(patch)
                    self.save()
                    return copy.deepcopy(item)
            raise KeyError(f"Conflitto non trovato: {conflict_id}")

    def remove_conflict(self, conflict_id: str) -> None:
        with self._file_lock:
            items = self._data.setdefault("conflicts", [])
            self._data["conflicts"] = [
                c for c in items if c.get("conflict_id") != conflict_id
            ]
            self.save()

# --- Async API (future worker / asyncio pipeline) ---

    async def areload(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.reload)

    async def asave(self) -> None:
        await asyncio.to_thread(self.save)

    async def aupdate_chunk(self, chunk_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.update_chunk, chunk_id, patch)

    async def aupdate_rolling_memory(self, patch: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.update_rolling_memory, patch)

    def __enter__(self) -> V2MapManager:
        self._file_lock.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self._file_lock.release()
