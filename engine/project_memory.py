"""
Path memoria per progetto UI — projects/<slug>/ (blueprint).

Output workflow: 03_OUTPUT/<workflow_name>/
State / manifest / indice output: 04_MEMORY/

V2 Agentic RAG: layout parallelo sotto lo stesso slug (00_PROJECT … 05_OUTPUT).
Non sostituisce i path V1 (01_INGEST, 03_OUTPUT legacy) finché la migrazione non è attiva.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

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

# --- V2 directory segments (V2_MULTIMODAL_INGESTION_ARCHITECTURE.md §2) ---
V2_PROJECT_META_DIR: Final = "00_PROJECT"
V2_RAW_DIR: Final = "01_RAW"
V2_STAGING_DIR: Final = "02_STAGING"
V2_KNOWLEDGE_DIR: Final = "03_KNOWLEDGE"
V2_MEMORY_DIR: Final = "04_MEMORY"
V2_OUTPUT_DIR: Final = "05_OUTPUT"
V2_LOGS_DIR: Final = "logs"

V2_RAW_SUBDIRS: Final[tuple[str, ...]] = (
    "incoming",
    "processed",
    "rejected",
    "quarantine",
)

V2_KNOWLEDGE_SUBDIRS: Final[tuple[str, ...]] = (
    "canonical",
    "entities",
    "facts",
    "tags",
    "graph",
    "temporal",
)

V2_OUTPUT_SUBDIRS: Final[tuple[str, ...]] = (
    "reports",
    "reviews",
    "merged_docs",
    "conflict_documents",
    "exports",
)

V2_MEMORY_FILES: Final[tuple[str, ...]] = (
    "semantic_memory.json",
    "resolution_memory.json",
    "ingestion_registry.json",
)

V2_STAGING_REL_DIRS: Final[tuple[str, ...]] = (
    "original",
    "extracted/text",
    "extracted/images",
    "extracted/tables",
    "extracted/ocr",
    "extracted/metadata",
    "chunks",
    "enriched",
    "rolling_memory",
    "conflicts/unresolved",
    "conflicts/resolved",
    "audit",
)

V2_MAP_JSON = "map.json"
V2_CONFLICT_LOG_JSON = "conflict_log.json"

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


def flow_state_path(slug: str, flow_name: str) -> Path:
    d = memory_dir(slug) / "flows"
    d.mkdir(parents=True, exist_ok=True)
    safe = _sanitize_segment(flow_name, fallback="default")
    return d / f"{safe}.json"


def flow_definition_path(slug: str, flow_name: str) -> Path:
    d = memory_dir(slug) / "flows"
    d.mkdir(parents=True, exist_ok=True)
    safe = _sanitize_segment(flow_name, fallback="default")
    return d / f"{safe}.yaml"


def load_flow_state(slug: str, flow_name: str) -> dict[str, Any]:
    path = flow_state_path(slug, flow_name)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "flow_name": flow_name,
        "version": 1,
        "steps": [],
        "last_step": None,
    }


def save_flow_state(slug: str, flow_name: str, state: dict[str, Any]) -> None:
    atomic_write_json(flow_state_path(slug, flow_name), state)


def load_flow_definition(slug: str, flow_name: str) -> dict[str, Any]:
    path = flow_definition_path(slug, flow_name)
    if not path.is_file():
        raise FileNotFoundError(f"Flow definition mancante: {path}")
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError("PyYAML richiesto per flow YAML: pip install pyyaml") from e
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Flow YAML non valido: {path}")
    if data.get("version") not in (1, "1"):
        raise ValueError(f"Versione flow non supportata: {data.get('version')}")
    from engine.workflow_runner import WorkflowRunner

    runner = WorkflowRunner()
    for step in data.get("steps") or []:
        wf = step.get("workflow")
        if wf and runner.get_workflow(str(wf)) is None:
            raise ValueError(f"Workflow non registrato nel flow: {wf}")
    return data


def ingest_subdir(slug: str, stem: str) -> Path:
    return ingest_dir(slug) / stem


def _sanitize_segment(name: str, *, fallback: str = "item") -> str:
    """Nome cartella/file sicuro (no path traversal)."""
    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    safe = _SAFE_NAME_RE.sub("_", raw).strip("._")
    return safe or fallback


def _sanitize_document_id(document_id: str) -> str:
    """ID documento V2 — segmento singolo, safe per filesystem Windows."""
    return _sanitize_segment(document_id, fallback="document")


def _mkdir_all(parent: Path, relative: str = "") -> Path:
    if not relative:
        target = parent
    else:
        parts = [p for p in relative.replace("\\", "/").split("/") if p]
        target = parent.joinpath(*parts) if parts else parent
    target.mkdir(parents=True, exist_ok=True)
    return target


# --- V2 path helpers (progetto) ---


def v2_project_meta_dir(slug: str) -> Path:
    return _mkdir_all(project_dir(slug), V2_PROJECT_META_DIR)


def v2_raw_dir(slug: str) -> Path:
    return _mkdir_all(project_dir(slug), V2_RAW_DIR)


def v2_raw_subdir(slug: str, bucket: str) -> Path:
    safe = _sanitize_segment(bucket, fallback="incoming")
    if safe not in V2_RAW_SUBDIRS:
        raise ValueError(f"Bucket RAW V2 non valido: {bucket!r}")
    return _mkdir_all(v2_raw_dir(slug), safe)


def v2_staging_root(slug: str) -> Path:
    return _mkdir_all(project_dir(slug), V2_STAGING_DIR)


def v2_document_staging_dir(slug: str, document_id: str) -> Path:
    doc_id = _sanitize_document_id(document_id)
    return _mkdir_all(v2_staging_root(slug), doc_id)


def v2_knowledge_dir(slug: str) -> Path:
    return _mkdir_all(project_dir(slug), V2_KNOWLEDGE_DIR)


def v2_knowledge_subdir(slug: str, area: str) -> Path:
    safe = _sanitize_segment(area, fallback="canonical")
    if safe not in V2_KNOWLEDGE_SUBDIRS:
        raise ValueError(f"Area knowledge V2 non valida: {area!r}")
    return _mkdir_all(v2_knowledge_dir(slug), safe)


def v2_memory_dir(slug: str) -> Path:
    """Memoria V2 — stesso nome cartella V1 (`04_MEMORY`), helper esplicito per pipeline V2."""
    return memory_dir(slug)


def v2_output_root(slug: str) -> Path:
    return _mkdir_all(project_dir(slug), V2_OUTPUT_DIR)


def v2_output_subdir(slug: str, area: str) -> Path:
    safe = _sanitize_segment(area, fallback="reports")
    if safe not in V2_OUTPUT_SUBDIRS:
        raise ValueError(f"Area output V2 non valida: {area!r}")
    return _mkdir_all(v2_output_root(slug), safe)


def v2_logs_dir(slug: str) -> Path:
    return _mkdir_all(project_dir(slug), V2_LOGS_DIR)


@dataclass(frozen=True, slots=True)
class V2StagingLayout:
    """Path assoluti dell'albero staging per un singolo documento V2."""

    root: Path
    original: Path
    extracted_text: Path
    extracted_images: Path
    extracted_tables: Path
    extracted_ocr: Path
    extracted_metadata: Path
    chunks: Path
    enriched: Path
    rolling_memory: Path
    conflicts: Path
    conflicts_unresolved: Path
    conflicts_resolved: Path
    audit: Path
    map_json: Path
    conflict_log_json: Path


def setup_v2_staging_dirs(slug: str, document_id: str) -> V2StagingLayout:
    """
    Crea l'albero staging V2 per un documento (§2 V2 architecture).

    Struttura:
      02_STAGING/<document_id>/
        original/, extracted/{text,images,tables,ocr,metadata}/,
        chunks/, enriched/, rolling_memory/,
        conflicts/{unresolved,resolved}/, audit/, map.json
    """
    doc_root = v2_document_staging_dir(slug, document_id)
    for rel in V2_STAGING_REL_DIRS:
        _mkdir_all(doc_root, rel)

    layout = V2StagingLayout(
        root=doc_root,
        original=doc_root / "original",
        extracted_text=doc_root / "extracted" / "text",
        extracted_images=doc_root / "extracted" / "images",
        extracted_tables=doc_root / "extracted" / "tables",
        extracted_ocr=doc_root / "extracted" / "ocr",
        extracted_metadata=doc_root / "extracted" / "metadata",
        chunks=doc_root / "chunks",
        enriched=doc_root / "enriched",
        rolling_memory=doc_root / "rolling_memory",
        conflicts=doc_root / "conflicts",
        conflicts_unresolved=doc_root / "conflicts" / "unresolved",
        conflicts_resolved=doc_root / "conflicts" / "resolved",
        audit=doc_root / "audit",
        map_json=doc_root / V2_MAP_JSON,
        conflict_log_json=doc_root / "conflicts" / V2_CONFLICT_LOG_JSON,
    )

    if not layout.map_json.is_file():
        from engine.v2_map_manager import V2MapManager

        V2MapManager(
            layout.map_json,
            auto_create=True,
            document_id=_sanitize_document_id(document_id),
        )

    if not layout.conflict_log_json.is_file():
        atomic_write_json(
            layout.conflict_log_json,
            {"version": 1, "entries": []},
        )

    logger.info(
        "V2 staging creato: slug=%s document_id=%s root=%s",
        slug,
        _sanitize_document_id(document_id),
        doc_root,
    )
    return layout


def setup_v2_project_layout(slug: str) -> dict[str, Path]:
    """
    Scaffold layout progetto V2 (cartelle top-level + sotto-alberi RAW/KNOWLEDGE/OUTPUT/MEMORY/logs).
    Idempotente — non rimuove path V1 esistenti (01_INGEST, 03_OUTPUT legacy).
    """
    root = project_dir(slug)
    root.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {
        "root": root,
        "project_meta": v2_project_meta_dir(slug),
        "raw": v2_raw_dir(slug),
        "staging": v2_staging_root(slug),
        "knowledge": v2_knowledge_dir(slug),
        "memory": v2_memory_dir(slug),
        "output": v2_output_root(slug),
        "logs": v2_logs_dir(slug),
    }

    for sub in V2_RAW_SUBDIRS:
        paths[f"raw_{sub}"] = v2_raw_subdir(slug, sub)

    for sub in V2_KNOWLEDGE_SUBDIRS:
        paths[f"knowledge_{sub}"] = v2_knowledge_subdir(slug, sub)

    for sub in V2_OUTPUT_SUBDIRS:
        paths[f"output_{sub}"] = v2_output_subdir(slug, sub)

    mem = v2_memory_dir(slug)
    for fname in V2_MEMORY_FILES:
        fpath = mem / fname
        paths[f"memory_{fname.replace('.json', '')}"] = fpath
        if not fpath.is_file():
            atomic_write_json(fpath, {"version": 1})

    for log_name in ("ingestion.log", "vision.log", "orchestration.log"):
        log_path = paths["logs"] / log_name
        paths[f"log_{log_name.replace('.log', '')}"] = log_path
        log_path.touch(exist_ok=True)

    logger.info("V2 project layout creato: slug=%s root=%s", slug, root)
    return paths


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
