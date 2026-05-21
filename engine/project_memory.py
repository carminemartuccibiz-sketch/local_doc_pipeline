"""
Path memoria per progetto UI — projects/<slug>/04_MEMORY/ (blueprint).
"""
from __future__ import annotations

from pathlib import Path

from config import PIPELINE_ROOT

PROJECTS_ROOT = PIPELINE_ROOT / "projects"

INGEST_MANIFEST = "ingest_manifest.json"
PIPELINE_STATE = "pipeline_state.json"
GAP_ALLM_STATE = "gap_allm_state.json"
GAP_REPORT_NAME = "Gap_Report_Generale.md"


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


def ingest_subdir(slug: str, stem: str) -> Path:
    return ingest_dir(slug) / stem
