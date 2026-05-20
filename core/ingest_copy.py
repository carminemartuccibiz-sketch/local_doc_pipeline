"""
Copia sicura (non distruttiva) dei sorgenti grezzi in 01_RAW_INGEST con deduplica MD5.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from config import ACCEPTED_EXTENSIONS, DEFAULT_SOURCE_ROOT, EXCLUDE_DIR_NAMES, INGEST_COPY_SOURCES
from core.paths import ingest_manifest_path, raw_ingest_dir
from core.progress import progress_bar

logger = logging.getLogger(__name__)

_CHUNK_HASH = 1024 * 1024


@dataclass
class IngestCopyResult:
    copied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            block = f.read(_CHUNK_HASH)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _load_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(path: Path, manifest: dict[str, dict[str, str]]) -> None:
    from core.file_io import atomic_write_json

    atomic_write_json(path, manifest)


def _should_copy(path: Path) -> bool:
    if path.suffix.lower() not in ACCEPTED_EXTENSIONS:
        return False
    for part in path.parts:
        if part in EXCLUDE_DIR_NAMES:
            return False
    return True


def _is_duplicate(
    dest: Path,
    src: Path,
    manifest: dict[str, dict[str, str]],
    dest_key: str,
) -> bool:
    if not dest.is_file():
        return False
    src_size = src.stat().st_size
    dest_size = dest.stat().st_size
    if src_size != dest_size:
        return False
    src_hash = file_md5(src)
    if manifest.get(dest_key, {}).get("md5") == src_hash:
        return True
    if dest.stat().st_size == src_size:
        try:
            return file_md5(dest) == src_hash
        except OSError:
            return False
    return False


def populate_raw_ingest(
    *,
    repo_root: Path = DEFAULT_SOURCE_ROOT,
    ingest_root: Path | None = None,
    sources: tuple[tuple[str, str], ...] | None = None,
    dry_run: bool = False,
) -> IngestCopyResult:
    """
    Copia file da Takeout, chat export, Raw docs, ecc. in 01_RAW_INGEST/<label>/...
    Skip se MD5+nome+dimensione coincidono (manifest + file destinazione).
    """
    dest_root = ingest_root or raw_ingest_dir(repo_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    manifest_path = ingest_manifest_path(repo_root)
    manifest = _load_manifest(manifest_path)
    src_defs = sources or INGEST_COPY_SOURCES
    result = IngestCopyResult()

    # Precollect per progress bar globale
    jobs: list[tuple[str, Path, Path, str]] = []
    for label, rel in src_defs:
        src_base = (repo_root / rel).resolve()
        if not src_base.exists():
            logger.warning("Sorgente ingest assente, skip: %s", rel)
            continue
        files = (
            [src_base]
            if src_base.is_file()
            else [p for p in src_base.rglob("*") if p.is_file() and _should_copy(p)]
        )
        for src in files:
            try:
                rel_under = src.relative_to(src_base) if src_base.is_dir() else Path(src.name)
            except ValueError:
                rel_under = Path(src.name)
            dest = dest_root / label / rel_under
            jobs.append((label, src, dest, f"{label}/{rel_under.as_posix()}"))

    print(f"\nIngest: {len(jobs)} candidati (deduplica MD5)...")
    for _label, src, dest, dest_key in progress_bar(jobs, desc="Ingest copy", unit="file"):
        if _is_duplicate(dest, src, manifest, dest_key):
            result.skipped += 1
            continue

        if dry_run:
            logger.info("  [dry-run] %s -> %s", src, dest)
            result.copied += 1
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            digest = file_md5(dest)
            manifest[dest_key] = {
                "md5": digest,
                "size": str(dest.stat().st_size),
                "source": str(src),
            }
            result.copied += 1
        except OSError as e:
            msg = f"{src}: {e}"
            result.errors.append(msg)
            logger.error("%s", msg)

    if not dry_run:
        _save_manifest(manifest_path, manifest)

    logger.info(
        "Ingest copy fine: copiati=%d skipped=%d errori=%d -> %s",
        result.copied,
        result.skipped,
        len(result.errors),
        dest_root,
    )
    return result
