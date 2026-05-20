"""
State management fault-tolerant — 02_SESSION_MEMORY/pipeline_state.json
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.file_io import atomic_write_json
from core.paths import session_state_path

logger = logging.getLogger(__name__)

FileStatus = Literal["pending", "processing", "completed", "failed", "skipped"]

STATE_VERSION = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "phase": "idle",
        "started_at": None,
        "updated_at": _utc_now(),
        "current_file": None,
        "current_chunk": 0,
        "files": {},
        "ingest": {},
    }


class PipelineSessionState:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or session_state_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = _empty_state()
        self._dirty = False
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            self.data = _empty_state()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = {**_empty_state(), **raw}
                self.data.setdefault("files", {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("State corrotto, reset: %s", e)
            self.data = _empty_state()

    def save(self, *, force: bool = False) -> None:
        if not force and not self._dirty:
            return
        self.data["updated_at"] = _utc_now()
        try:
            atomic_write_json(self.path, self.data)
            self._dirty = False
        except OSError as e:
            logger.error("Salvataggio state fallito: %s", e)
            raise

    def begin_pipeline(self, phase: str = "gap_analysis") -> None:
        if not self.data.get("started_at"):
            self.data["started_at"] = _utc_now()
        self.data["phase"] = phase
        self._dirty = True
        self.save(force=True)

    def register_file_pending(self, rel_key: str, *, md5: str | None = None) -> None:
        files = self.data.setdefault("files", {})
        if rel_key not in files:
            files[rel_key] = {
                "status": "pending",
                "chunks_total": 0,
                "chunks_done": 0,
                "md5": md5,
            }
            self._dirty = True

    def reconcile_with_ingest_keys(self, valid_keys: set[str]) -> int:
        """Rimuove dal state file fantasma (es. test file_0.md) non presenti in 01_RAW_INGEST."""
        files = self.data.setdefault("files", {})
        stale = [k for k in files if k not in valid_keys]
        for k in stale:
            del files[k]
        if stale:
            self._dirty = True
            self.save(force=True)
            logger.info("State: rimossi %d path obsoleti", len(stale))
        return len(stale)

    def register_all_files_pending(self, keys: list[str]) -> None:
        """Registra tutti i file con una sola scrittura su disco."""
        files = self.data.setdefault("files", {})
        added = 0
        for rel_key in keys:
            if rel_key not in files:
                files[rel_key] = {
                    "status": "pending",
                    "chunks_total": 0,
                    "chunks_done": 0,
                    "md5": None,
                }
                added += 1
        self.data["files_total"] = len(files)
        self.data["files_pending"] = sum(
            1 for e in files.values() if e.get("status") in ("pending", "failed")
        )
        if added or self._dirty:
            self._dirty = True
            self.save(force=True)
        logger.info("State: %d file in coda (%d nuovi registrati)", len(files), added)

    def mark_processing(
        self,
        rel_key: str,
        *,
        chunk_index: int = 0,
        chunks_total: int = 1,
    ) -> None:
        files = self.data.setdefault("files", {})
        entry = files.setdefault(rel_key, {})
        entry["status"] = "processing"
        entry["chunks_total"] = chunks_total
        entry["chunks_done"] = chunk_index
        entry["updated_at"] = _utc_now()
        self.data["current_file"] = rel_key
        self.data["current_chunk"] = chunk_index
        self._dirty = True
        self.save(force=True)

    def mark_chunk_done(
        self,
        rel_key: str,
        chunk_index: int,
        *,
        flush_every: int = 1,
    ) -> None:
        files = self.data.setdefault("files", {})
        entry = files.setdefault(rel_key, {})
        entry["chunks_done"] = chunk_index + 1
        entry["status"] = "processing"
        self.data["current_chunk"] = chunk_index + 1
        self._dirty = True
        if flush_every <= 1 or (chunk_index + 1) % flush_every == 0:
            self.save(force=True)

    def mark_completed(self, rel_key: str) -> None:
        files = self.data.setdefault("files", {})
        entry = files.setdefault(rel_key, {})
        entry["status"] = "completed"
        entry["updated_at"] = _utc_now()
        self.data["current_file"] = None
        self.data["current_chunk"] = 0
        completed = sum(1 for e in files.values() if e.get("status") == "completed")
        self.data["files_completed"] = completed
        self._dirty = True
        self.save(force=True)

    def mark_failed(self, rel_key: str, error: str) -> None:
        files = self.data.setdefault("files", {})
        entry = files.setdefault(rel_key, {})
        entry["status"] = "failed"
        entry["error"] = error[:500]
        entry["updated_at"] = _utc_now()
        self.data["current_file"] = None
        self._dirty = True
        self.save(force=True)

    def file_status(self, rel_key: str) -> FileStatus:
        entry = self.data.get("files", {}).get(rel_key, {})
        return entry.get("status", "pending")

    def should_process(self, rel_key: str) -> bool:
        st = self.file_status(rel_key)
        return st in ("pending", "processing", "failed")

    def resume_chunk_index(self, rel_key: str) -> int:
        if self.file_status(rel_key) != "processing":
            return 0
        return int(self.data.get("current_chunk") or 0)

    def interrupted_file(self) -> str | None:
        cf = self.data.get("current_file")
        if not cf:
            return None
        if self.file_status(cf) == "processing":
            return cf
        return None

    def record_ingest(self, *, copied: int, skipped: int, errors: int) -> None:
        self.data["ingest"] = {
            "last_run": _utc_now(),
            "copied": copied,
            "skipped": skipped,
            "errors": errors,
        }
        self._dirty = True
        self.save(force=True)

    def pending_or_resume_files(self, ordered_keys: list[str]) -> list[str]:
        interrupted = self.interrupted_file()
        out: list[str] = []
        seen: set[str] = set()

        if interrupted and interrupted in ordered_keys:
            out.append(interrupted)
            seen.add(interrupted)

        for key in ordered_keys:
            if key in seen:
                continue
            if self.should_process(key):
                out.append(key)
                seen.add(key)
        return out

    def stats(self) -> dict[str, int]:
        files = self.data.get("files", {})
        return {
            "total": len(files),
            "completed": sum(1 for e in files.values() if e.get("status") == "completed"),
            "pending": sum(1 for e in files.values() if e.get("status") == "pending"),
            "processing": sum(1 for e in files.values() if e.get("status") == "processing"),
            "failed": sum(1 for e in files.values() if e.get("status") == "failed"),
        }
