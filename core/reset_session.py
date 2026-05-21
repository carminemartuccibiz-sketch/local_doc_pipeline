"""
Reset sessione gap analysis (state, report, cache AnythingLLM locale).
Non cancella 01_RAW_INGEST salvo opzione include_ingest.
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.gap_allm import clear_sot_sync_state
from core.paths import gap_reports_dir, ingest_manifest_path, session_state_path

logger = logging.getLogger(__name__)


def reset_gap_session(
    repo_root: Path,
    *,
    include_reports: bool = True,
    include_allm_cache: bool = True,
    keep_allm_cache: bool = False,
    include_ingest_manifest: bool = False,
    requeue_all_pending: bool = True,
) -> list[str]:
    """
    Pulisce artefatti di esecuzione. Ritorna elenco azioni eseguite.
    """
    actions: list[str] = []
    state_path = session_state_path(repo_root)

    if state_path.is_file():
        if requeue_all_pending:
            import json

            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                files = data.get("files") or {}
                for entry in files.values():
                    if isinstance(entry, dict) and entry.get("status") in (
                        "processing",
                        "failed",
                        "completed",
                    ):
                        entry["status"] = "pending"
                        entry["chunks_done"] = 0
                        entry["chunks_total"] = 0
                        entry.pop("error", None)
                data["current_file"] = None
                data["current_chunk"] = 0
                data["phase"] = "idle"
                from core.file_io import atomic_write_json

                atomic_write_json(state_path, data)
                actions.append(
                    f"State resettato (tutti pending): {state_path.name}"
                )
            except (json.JSONDecodeError, OSError):
                state_path.unlink()
                actions.append(f"State rimosso: {state_path}")
        else:
            state_path.unlink()
            actions.append(f"State rimosso: {state_path}")
    else:
        actions.append("Nessun pipeline_state.json da resettare")

    if include_reports:
        reports = gap_reports_dir(repo_root)
        if reports.is_dir():
            for p in reports.glob("GAP_*.md"):
                p.unlink()
            gen = reports / "Gap_Report_Generale.md"
            if gen.is_file():
                gen.unlink()
            actions.append(f"Report gap rimossi: {reports}")

    if include_allm_cache and not keep_allm_cache:
        clear_sot_sync_state()
        actions.append("Cache upload SOT (gap_allm_state) rimossa")
    elif keep_allm_cache:
        actions.append("Cache upload SOT (gap_allm_state) CONSERVATA")

    if include_ingest_manifest:
        manifest = ingest_manifest_path(repo_root)
        if manifest.is_file():
            manifest.unlink()
            actions.append(f"Manifest ingest rimosso: {manifest.name}")

    return actions
