"""
Gestione progetti runtime sotto projects/<slug>/ (FASE 6).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PIPELINE_ROOT
from core.file_io import atomic_write_json
from engine.project_memory import ingest_manifest_path

PROJECTS_ROOT = PIPELINE_ROOT / "projects"
VALID_ROLES = frozenset({"SOT", "Reference", "Raw"})
PROJECT_DIRS = ("01_INGEST", "02_REFERENCE", "03_OUTPUT", "04_MEMORY")
FILE_ROLES_NAME = "file_roles.json"


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s or "project"


def _project_path(slug: str) -> Path:
    return PROJECTS_ROOT / slug


def _roles_path(project_dir: Path) -> Path:
    return project_dir / "02_REFERENCE" / FILE_ROLES_NAME


def list_projects() -> list[dict[str, Any]]:
    if not PROJECTS_ROOT.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(PROJECTS_ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        cfg = child / "project.json"
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                data.setdefault("slug", child.name)
                out.append(data)
            except (json.JSONDecodeError, OSError):
                out.append({"slug": child.name, "name": child.name, "workflow": "ingest"})
    return out


def create_project(*, name: str, workflow: str = "ingest") -> dict[str, Any]:
    slug = _slugify(name)
    base = _project_path(slug)
    if base.exists():
        n = 2
        while _project_path(f"{slug}-{n}").exists():
            n += 1
        slug = f"{slug}-{n}"
        base = _project_path(slug)

    for sub in PROJECT_DIRS:
        (base / sub).mkdir(parents=True, exist_ok=True)

    atomic_write_json(_roles_path(base), {})

    meta = {
        "name": name.strip() or slug,
        "slug": slug,
        "workflow": workflow,
        "hardware_profile": "eco",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(base / "project.json", meta)
    return meta


def load_project(slug: str) -> dict[str, Any]:
    cfg = _project_path(slug) / "project.json"
    if not cfg.is_file():
        raise FileNotFoundError(f"Progetto non trovato: {slug}")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data.setdefault("slug", slug)
    return data


def load_file_roles(slug: str) -> dict[str, str]:
    path = _roles_path(_project_path(slug))
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if v in VALID_ROLES}
    except (json.JSONDecodeError, OSError):
        return {}


def set_file_role(slug: str, file_path: str, role: str) -> dict[str, str]:
    if role not in VALID_ROLES:
        raise ValueError(f"Ruolo non valido: {role}")
    rel = file_path.replace("\\", "/").lstrip("/")
    roles = load_file_roles(slug)
    roles[rel] = role
    atomic_write_json(_roles_path(_project_path(slug)), roles)
    return roles


def _iter_project_files(project_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for sub in PROJECT_DIRS:
        root = project_dir / sub
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name in (FILE_ROLES_NAME, "project.json", "chunks.json"):
                continue
            if path.name.startswith(".") or path.suffix == ".tmp":
                continue
            rel = path.relative_to(project_dir).as_posix()
            files.append(
                {
                    "path": rel,
                    "name": path.name,
                    "area": sub,
                    "size": path.stat().st_size,
                }
            )
    return files


def get_project_detail(slug: str) -> dict[str, Any]:
    project_dir = _project_path(slug)
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Progetto non trovato: {slug}")

    meta = load_project(slug)
    roles = load_file_roles(slug)
    files = _iter_project_files(project_dir)
    for f in files:
        f["role"] = roles.get(f["path"], "Raw")

    return {**meta, "files": files, "file_roles": roles}


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_ingest_manifest(slug: str) -> dict[str, Any]:
    path = ingest_manifest_path(slug)
    if not path.is_file():
        return {"version": 1, "by_md5": {}, "files": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("by_md5", {})
            raw.setdefault("files", {})
            return raw
    except (json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "by_md5": {}, "files": {}}


def save_ingest_manifest(slug: str, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(ingest_manifest_path(slug), manifest)


def _ingest_complete(slug: str, stem: str) -> bool:
    sub = _project_path(slug) / "01_INGEST" / stem
    return (sub / "chunks.json").is_file() and (sub / "analysis.md").is_file()


def list_ingest_sources(
    slug: str,
    *,
    skip_duplicates: bool = True,
    skip_completed: bool = True,
) -> list[Path]:
    """
    File top-level in 01_INGEST da processare.
    Deduplica MD5 (manifest 04_MEMORY) e salta cartelle già analizzate.
    """
    ingest = _project_path(slug) / "01_INGEST"
    if not ingest.is_dir():
        return []

    manifest = load_ingest_manifest(slug)
    by_md5: dict[str, Any] = manifest.setdefault("by_md5", {})
    files_meta: dict[str, Any] = manifest.setdefault("files", {})
    sources: list[Path] = []
    dirty = False

    for path in sorted(ingest.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue

        md5 = file_md5(path)
        prev = by_md5.get(md5)
        if skip_duplicates and prev and prev.get("name") != path.name:
            continue

        if skip_completed and _ingest_complete(slug, path.stem):
            by_md5[md5] = {
                "name": path.name,
                "status": "completed",
                "md5": md5,
            }
            files_meta[path.name] = {"md5": md5, "status": "completed"}
            dirty = True
            continue

        sources.append(path)
        by_md5[md5] = {"name": path.name, "status": "pending", "md5": md5}
        files_meta[path.name] = {"md5": md5, "status": "pending"}
        dirty = True

    if dirty:
        save_ingest_manifest(slug, manifest)

    return sources


def mark_ingest_file_done(slug: str, path: Path) -> None:
    """Aggiorna manifest dopo ingest sliding window riuscito."""
    manifest = load_ingest_manifest(slug)
    md5 = file_md5(path)
    manifest.setdefault("by_md5", {})[md5] = {
        "name": path.name,
        "status": "completed",
        "md5": md5,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest.setdefault("files", {})[path.name] = {
        "md5": md5,
        "status": "completed",
    }
    save_ingest_manifest(slug, manifest)
