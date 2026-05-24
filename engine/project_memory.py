"""
Path memoria per progetto UI — projects/<slug>/ (blueprint).

Output workflow: 03_OUTPUT/<workflow_name>/
State / manifest / indice output: 04_MEMORY/
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config import PIPELINE_ROOT
from core.file_io import atomic_write_json

if TYPE_CHECKING:
    from engine.orchestrator import OrchestratorState

logger = logging.getLogger(__name__)

PROJECTS_ROOT = PIPELINE_ROOT / "projects"

INGEST_MANIFEST = "ingest_manifest.json"
PIPELINE_STATE = "pipeline_state.json"
GAP_ALLM_STATE = "gap_allm_state.json"
GAP_REPORT_NAME = "Gap_Report_Generale.md"
WORKFLOW_OUTPUTS_INDEX = "workflow_outputs.json"

_INDEX_LOCK = threading.RLock()
_SAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def project_dir(slug: str) -> Path:
    return PROJECTS_ROOT / slug


def memory_dir(slug: str) -> Path:
    d = project_dir(slug) / "04_MEMORY"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ingest_dir(slug: str) -> Path:
    return project_dir(slug) / "01_INGEST"


def output_dir(slug: str) -> Path:
    d = project_dir(slug) / "03_OUTPUT"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pipeline_state_path(slug: str) -> Path:
    return memory_dir(slug) / PIPELINE_STATE


def ingest_manifest_path(slug: str) -> Path:
    return memory_dir(slug) / INGEST_MANIFEST


def gap_allm_state_path(slug: str) -> Path:
    return memory_dir(slug) / GAP_ALLM_STATE


def gap_report_path(slug: str) -> Path:
    return output_dir(slug) / GAP_REPORT_NAME


def workflow_outputs_index_path(slug: str) -> Path:
    return memory_dir(slug) / WORKFLOW_OUTPUTS_INDEX


def ingest_subdir(slug: str, stem: str) -> Path:
    return ingest_dir(slug) / stem


def _sanitize_segment(name: str, *, fallback: str = "item") -> str:
    """Nome cartella/file sicuro (no path traversal)."""
    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    safe = _SAFE_NAME_RE.sub("_", raw).strip("._")
    return safe or fallback


def workflow_output_dir(slug: str, workflow_name: str) -> Path:
    """Cartella dedicata: projects/<slug>/03_OUTPUT/<workflow_name>/"""
    wf = _sanitize_segment(workflow_name, fallback="workflow")
    d = output_dir(slug) / wf
    d.mkdir(parents=True, exist_ok=True)
    return d


def workflow_output_path(slug: str, workflow_name: str, filename: str) -> Path:
    """Path destinazione file in 03_OUTPUT (crea cartelle workflow)."""
    fname = _sanitize_segment(filename, fallback="output.md")
    if not fname.lower().endswith((".md", ".txt", ".json")):
        fname = f"{fname}.md"
    return workflow_output_dir(slug, workflow_name) / fname


def _format_markdown_body(
    content: str,
    *,
    header: str | None = None,
    workflow_name: str | None = None,
    source_file: str | None = None,
) -> str:
    body = (content or "").strip()
    if not body:
        body = ""
    parts: list[str] = []
    if header:
        parts.append(header.strip())
    elif workflow_name:
        title = workflow_name.replace("_", " ").title()
        parts.append(f"# {title}")
        meta: list[str] = []
        if source_file:
            meta.append(f"- **Sorgente:** `{source_file}`")
        meta.append(
            f"- **Generato:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        parts.append("\n".join(meta))
    if parts:
        return "\n\n".join(parts) + ("\n\n" + body if body else "\n")
    return body + ("\n" if body and not body.endswith("\n") else "")


def _relative_output_path(slug: str, absolute: Path) -> str:
    base = output_dir(slug).resolve()
    try:
        return absolute.resolve().relative_to(base).as_posix()
    except ValueError:
        return absolute.name


def _append_workflow_output_index(
    slug: str,
    *,
    workflow_name: str,
    path: Path,
    size_bytes: int,
) -> None:
    """Traccia output in 04_MEMORY/workflow_outputs.json (thread-safe)."""
    index_path = workflow_outputs_index_path(slug)
    rel = _relative_output_path(slug, path)
    entry = {
        "workflow": workflow_name,
        "path": rel,
        "absolute": str(path.resolve()),
        "size_bytes": size_bytes,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    with _INDEX_LOCK:
        memory_dir(slug)
        if index_path.is_file():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {"outputs": []}
        else:
            data = {"outputs": []}
        outputs: list[dict[str, Any]] = list(data.get("outputs") or [])
        outputs.append(entry)
        data["outputs"] = outputs[-500:]
        data["updated_at"] = entry["written_at"]
        atomic_write_json(index_path, data)


def save_workflow_output(
    slug: str,
    workflow_name: str,
    filename: str,
    content: str,
    *,
    as_markdown: bool = False,
    markdown_header: str | None = None,
    source_file: str | None = None,
    state: OrchestratorState | None = None,
    bump_progress: bool = False,
    current_file: str | None = None,
) -> Path:
    """
    Salva stringa o Markdown in 03_OUTPUT/<workflow_name>/<filename>.

    Se ``state`` è l'OrchestratorState attivo e ``bump_progress=True``, aggiorna
    ``files_completed`` / ``outputs_written`` sotto lock per la progress bar UI.

    I plugin workflow possono passare ``ctx["orchestrator"]`` o importare
    ``get_orchestrator_state()`` quando girano nel job worker.
    """
    out_path = workflow_output_path(slug, workflow_name, filename)
    text = (
        _format_markdown_body(
            content,
            header=markdown_header,
            workflow_name=workflow_name if as_markdown else None,
            source_file=source_file or current_file,
        )
        if as_markdown or markdown_header
        else content
    )
    if not text.endswith("\n"):
        text += "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")

    rel = _relative_output_path(slug, out_path)
    _append_workflow_output_index(
        slug,
        workflow_name=workflow_name,
        path=out_path,
        size_bytes=out_path.stat().st_size,
    )

    if state is not None:
        state.record_workflow_output(
            rel,
            workflow=workflow_name,
            bump_progress=bump_progress,
            current_file=current_file or source_file,
        )

    logger.info(
        "Workflow output salvato: slug=%s workflow=%s path=%s",
        slug,
        workflow_name,
        rel,
    )
    return out_path


def save_workflow_output_markdown(
    slug: str,
    workflow_name: str,
    filename: str,
    body: str,
    *,
    header: str | None = None,
    source_file: str | None = None,
    state: OrchestratorState | None = None,
    bump_progress: bool = False,
    current_file: str | None = None,
) -> Path:
    """Shortcut: salva Markdown formattato in 03_OUTPUT."""
    return save_workflow_output(
        slug,
        workflow_name,
        filename,
        body,
        as_markdown=True,
        markdown_header=header,
        source_file=source_file,
        state=state,
        bump_progress=bump_progress,
        current_file=current_file,
    )
