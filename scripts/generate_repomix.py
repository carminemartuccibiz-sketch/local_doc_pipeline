#!/usr/bin/env python3
"""
Genera _LLM_CONTEXT_DUMP.txt per handoff a Claude/Cursor.

1. Esegue update_dev_router.py (ROOTPAM con storico IA)
2. Concatena ROOTPAM in cima, poi sorgenti selezionati
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DMIP_ROOT = REPO_ROOT.parent / "dmip"
OUTPUT_PATH = REPO_ROOT / "_LLM_CONTEXT_DUMP.txt"
ROOTPAM_PATH = REPO_ROOT / "ROOTPAM.md"
ROUTER_SCRIPT = REPO_ROOT / "scripts" / "update_dev_router.py"

# Cartelle/file esclusi dal dump (runtime, binari, segreti)
EXCLUDE_DIRS = frozenset(
    {
        ".venv",
        ".git",
        "__pycache__",
        "data",
        "logs",
        "projects",
        "node_modules",
        ".cursor",
        ".idea",
        ".vscode",
    }
)
EXCLUDE_FILES = frozenset(
    {
        "_LLM_CONTEXT_DUMP.txt",
        ".env",
        ".env.local",
    }
)
INCLUDE_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".html",
        ".js",
        ".css",
        ".json",
        ".txt",
        ".ps1",
        ".bat",
        ".example",
    }
)
MAX_FILE_BYTES = 120_000


def run_router() -> None:
    if not ROUTER_SCRIPT.is_file():
        print(f"WARN: router assente: {ROUTER_SCRIPT}", file=sys.stderr)
        return
    subprocess.run(
        [sys.executable, str(ROUTER_SCRIPT)],
        cwd=str(REPO_ROOT),
        check=False,
    )


def should_include(path: Path) -> bool:
    if path.name in EXCLUDE_FILES:
        return False
    if path.suffix and path.suffix.lower() not in INCLUDE_SUFFIXES:
        return False
    parts = path.parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return False
    return True


def _iter_tree(root: Path, prefix: str) -> list[tuple[str, Path]]:
    """(display_path, absolute_path) sotto root."""
    out: list[tuple[str, Path]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if not should_include(rel):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        display = f"{prefix}/{rel.as_posix()}" if prefix else rel.as_posix()
        out.append((display, path))
    return out


def iter_source_files(*, include_dmip: bool = False) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for display, full in _iter_tree(REPO_ROOT, ""):
        if display == "ROOTPAM.md":
            continue
        files.append((display, full))
    if include_dmip:
        for display, full in _iter_tree(DMIP_ROOT, "dmip"):
            files.append((display, full))
    return files


def read_chunk(full: Path) -> str:
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"[read error: {e}]\n"


def build_dump(*, include_dmip: bool = False) -> str:
    run_router()

    parts: list[str] = []
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    parts.append("=" * 72)
    parts.append("DVAMOCLES Local AI Orchestrator — LLM CONTEXT DUMP")
    parts.append(f"Generated: {stamp}")
    if include_dmip:
        parts.append(f"DMIP: {DMIP_ROOT.as_posix()}")
    parts.append("=" * 72)
    parts.append("")

    if ROOTPAM_PATH.is_file():
        parts.append("=" * 72)
        parts.append("FILE: ROOTPAM.md (REPO MAP + AI DEV HISTORY)")
        parts.append("=" * 72)
        parts.append(read_chunk(ROOTPAM_PATH))
        parts.append("")

    for display, full in iter_source_files(include_dmip=include_dmip):
        parts.append("=" * 72)
        parts.append(f"FILE: {display}")
        parts.append("=" * 72)
        parts.append(read_chunk(full))
        parts.append("")

    return "\n".join(parts)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Genera _LLM_CONTEXT_DUMP.txt")
    parser.add_argument(
        "--include-dmip",
        action="store_true",
        help="Includi sorgenti da tools/dmip/ (sibling)",
    )
    args = parser.parse_args()
    dump = build_dump(include_dmip=args.include_dmip)
    OUTPUT_PATH.write_text(dump, encoding="utf-8", newline="\n")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Scritto {OUTPUT_PATH} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
