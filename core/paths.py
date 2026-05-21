"""
Layout cartelle sessione pipeline + discovery documenti SOT (gerarchia tier).
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Iterator

from config import ACCEPTED_EXTENSIONS, DEFAULT_SOURCE_ROOT, PIPELINE_ROOT, SOT_TIERS
from settings import GAP_SOT_LAST_DOCS_ONLY

DIR_RAW_INGEST = "01_RAW_INGEST"
DIR_SESSION_MEMORY = "02_SESSION_MEMORY"
DIR_GAP_REPORTS = "GAP_ANALYSIS_REPORTS"
DEFAULT_GAP_REPORT_NAME = "Gap_Report_Generale.md"

CANONICAL_SOT_DIR_NAMES: frozenset[str] = frozenset(
    {"LAST DOCS", "Documentazione vecchia", "SUITE", "SOT"}
)


def workspace_root(repo_root: Path | None = None) -> Path:
    return PIPELINE_ROOT


def raw_ingest_dir(repo_root: Path | None = None) -> Path:
    return workspace_root(repo_root) / DIR_RAW_INGEST


def gap_reports_dir(repo_root: Path | None = None) -> Path:
    return workspace_root(repo_root) / DIR_SESSION_MEMORY / DIR_GAP_REPORTS


def gap_report_path(
    repo_root: Path | None = None,
    report_name: str = DEFAULT_GAP_REPORT_NAME,
) -> Path:
    return gap_reports_dir(repo_root) / report_name


def gap_report_path_for_raw(raw_rel: str, repo_root: Path | None = None) -> Path:
    """Un report SPEC per file grezzo analizzato."""
    p = Path(raw_rel.replace("\\", "/"))
    stem = re.sub(r"[^\w\-.]+", "_", p.stem).strip("_")
    parent = re.sub(r"[^\w\-.]+", "_", p.parent.as_posix()).strip("_") if p.parent.parts else ""
    safe = f"{parent}_{stem}" if parent else stem
    if len(safe) > 120:
        safe = safe[:120]
    return gap_reports_dir(repo_root) / f"GAP_{safe}.md"


def session_memory_dir(repo_root: Path | None = None) -> Path:
    return workspace_root(repo_root) / DIR_SESSION_MEMORY


def session_state_path(repo_root: Path | None = None) -> Path:
    return session_memory_dir(repo_root) / "pipeline_state.json"


def ingest_manifest_path(repo_root: Path | None = None) -> Path:
    return session_memory_dir(repo_root) / "ingest_manifest.json"


def ensure_session_dirs(repo_root: Path | None = None) -> tuple[Path, Path]:
    raw = raw_ingest_dir(repo_root)
    reports = gap_reports_dir(repo_root)
    mem = session_memory_dir(repo_root)
    raw.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    mem.mkdir(parents=True, exist_ok=True)
    return raw, reports


def iter_ingest_files(ingest_root: Path) -> Iterator[Path]:
    """File in 01_RAW_INGEST (chiavi relative alla cartella ingest)."""
    if ingest_root.is_file():
        yield ingest_root.resolve()
        return
    for p in sorted(ingest_root.rglob("*")):
        if p.is_file() and p.suffix.lower() in ACCEPTED_EXTENSIONS:
            yield p.resolve()


def ingest_rel_key(path: Path, ingest_root: Path) -> str:
    try:
        return path.relative_to(ingest_root).as_posix()
    except ValueError:
        return path.name


def _tier_for_path(path: Path, bases: list[tuple[int, Path]]) -> int:
    for tier, base in bases:
        try:
            path.relative_to(base)
            return tier
        except ValueError:
            continue
    return 99


def _is_sot_file(path: Path) -> bool:
    posix = path.as_posix().replace("\\", "/")
    if "/SOT/" in posix:
        return True
    return _is_sot_file_by_frontmatter(str(path.resolve()))


@functools.lru_cache(maxsize=4096)
def _is_sot_file_by_frontmatter(resolved_path: str) -> bool:
    try:
        head = Path(resolved_path).read_bytes()[:512].decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return False
    return bool(
        re.search(r"^category:\s*SOT\b", head, re.MULTILINE | re.IGNORECASE)
        or re.search(r"^status:\s*AUTHORITATIVE", head, re.MULTILINE | re.IGNORECASE)
    )


def discover_sot_files(
    sot_paths: list[Path],
    *,
    repo_root: Path = DEFAULT_SOURCE_ROOT,
) -> list[Path]:
    """File SOT ordinati per tier (1 = LAST DOCS, 2 = Documentazione vecchia, ...)."""
    ordered = discover_sot_files_with_tier(sot_paths, repo_root=repo_root)
    return [p for _, p in ordered]


def discover_sot_files_with_tier(
    sot_paths: list[Path],
    *,
    repo_root: Path = DEFAULT_SOURCE_ROOT,
) -> list[tuple[int, Path]]:
    tier_bases: list[tuple[int, Path]] = []
    for entry in SOT_TIERS:
        tier = int(entry["tier"])
        rel = str(entry["rel"])
        for base in sot_paths:
            if base.name == Path(rel).name or base.as_posix().endswith(rel.replace("\\", "/")):
                tier_bases.append((tier, base.resolve()))
                break

    found: list[tuple[int, Path]] = []
    seen: set[str] = set()

    for base in sot_paths:
        if not base.exists():
            continue
        if base.is_file():
            if base.suffix.lower() in ACCEPTED_EXTENSIONS:
                key = base.resolve().as_posix()
                if key not in seen:
                    seen.add(key)
                    t = _tier_for_path(base.resolve(), tier_bases) if tier_bases else 1
                    found.append((t, base.resolve()))
            continue

        candidates = [
            p
            for p in base.rglob("*")
            if p.is_file() and p.suffix.lower() in ACCEPTED_EXTENSIONS
        ]
        for p in sorted(candidates, key=lambda x: x.as_posix().lower()):
            posix = p.as_posix()
            include = (
                "/SOT/" in posix
                or _is_sot_file(p)
                or base.name in CANONICAL_SOT_DIR_NAMES
            )
            if not include:
                continue
            key = p.resolve().as_posix()
            if key in seen:
                continue
            seen.add(key)
            t = _tier_for_path(p.resolve(), tier_bases) if tier_bases else 99
            found.append((t, p.resolve()))

    found.sort(key=lambda x: (x[0], x[1].as_posix().lower()))
    return found


def default_sot_directories(repo_root: Path = DEFAULT_SOURCE_ROOT) -> list[Path]:
    """Tier 1 poi tier 2 (ordine di caricamento Contesto Master)."""
    tiers = SOT_TIERS[:1] if GAP_SOT_LAST_DOCS_ONLY else SOT_TIERS
    dirs: list[Path] = []
    for entry in tiers:
        rel = str(entry["rel"])
        p = repo_root / rel
        if p.is_dir():
            dirs.append(p.resolve())
    return dirs


def build_compact_sot_index(sot_parts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Indice leggero (path + prime righe) — il testo completo passa via RAG."""
    compact: list[tuple[str, str]] = []
    for rel, body in sot_parts:
        head = "\n".join(body.strip().splitlines()[:12])
        compact.append(
            (rel, f"### {rel}\n{head}\n_(dettaglio via RAG — LAST DOCS + doc. vecchia)_\n")
        )
    return compact


def sot_tier_labels(repo_root: Path = DEFAULT_SOURCE_ROOT) -> list[str]:
    tiers = SOT_TIERS[:1] if GAP_SOT_LAST_DOCS_ONLY else SOT_TIERS
    labels: list[str] = []
    for entry in tiers:
        rel = str(entry["rel"])
        if (repo_root / rel).is_dir():
            labels.append(f"Tier {entry['tier']}: {entry['label']}")
    return labels


def iter_target_files(target: Path, *, repo_root: Path) -> Iterator[Path]:
    if target.is_file():
        yield target.resolve()
        return
    for p in sorted(target.rglob("*")):
        if p.is_file() and p.suffix.lower() in ACCEPTED_EXTENSIONS:
            yield p.resolve()
