#!/usr/bin/env python3
"""
Aggiorna ROOTPAM.md con lo storico sessioni IA da AI_DEV_SESSIONS/.

Parsing frontmatter YAML con regex/stdlib (no PyYAML).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = REPO_ROOT / "AI_DEV_SESSIONS"
ROOTPAM_PATH = REPO_ROOT / "ROOTPAM.md"
HISTORY_PREFIX = "↳ [🧠 STORICO AI]:"

_SKIP_FILES = frozenset({"_template_dev_log.md", "README.md"})


@dataclass
class DevSession:
    session_id: str
    date: str
    agent_used: str
    target_files: list[str]
    status: str
    next_steps: str
    source_file: str


def _parse_scalar(block: str, key: str) -> str:
    m = re.search(
        rf"^{re.escape(key)}:\s*(.+?)$",
        block,
        re.MULTILINE,
    )
    if not m:
        return ""
    val = m.group(1).strip()
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        val = val[1:-1]
    return val


def _parse_target_files(block: str) -> list[str]:
    m = re.search(r"^target_files:\s*$", block, re.MULTILINE)
    if not m:
        inline = _parse_scalar(block, "target_files")
        if inline:
            return [normalize_path(inline)]
        return []

    lines = block[m.end() :].splitlines()
    paths: list[str] = []
    for line in lines:
        if re.match(r"^\s*-\s+", line):
            item = re.sub(r"^\s*-\s+", "", line).strip().strip("'\"")
            if item:
                paths.append(normalize_path(item))
            continue
        if line.strip() and not line.startswith("#") and ":" in line and not line.startswith(" "):
            break
        if line.strip() and not line.startswith(" ") and not line.startswith("-"):
            break
    return paths


def parse_frontmatter(text: str) -> dict[str, str | list[str]]:
    if not text.lstrip().startswith("---"):
        return {}
    end = re.search(r"\n---\s*\n", text[3:])
    if not end:
        return {}
    block = text[3 : 3 + end.start()]
    return {
        "session_id": _parse_scalar(block, "session_id"),
        "date": _parse_scalar(block, "date"),
        "agent_used": _parse_scalar(block, "agent_used"),
        "target_files": _parse_target_files(block),
        "status": _parse_scalar(block, "status"),
        "next_steps": _parse_scalar(block, "next_steps"),
    }


def normalize_path(path: str) -> str:
    p = path.strip().replace("\\", "/").lstrip("/")
    return p


def load_sessions() -> list[DevSession]:
    if not SESSIONS_DIR.is_dir():
        return []

    sessions: list[DevSession] = []
    for md in sorted(SESSIONS_DIR.glob("*.md")):
        if md.name in _SKIP_FILES:
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta = parse_frontmatter(text)
        if not meta.get("target_files"):
            continue
        sessions.append(
            DevSession(
                session_id=str(meta.get("session_id") or md.stem),
                date=str(meta.get("date") or ""),
                agent_used=str(meta.get("agent_used") or "unknown"),
                target_files=list(meta.get("target_files") or []),
                status=str(meta.get("status") or ""),
                next_steps=str(meta.get("next_steps") or ""),
                source_file=md.name,
            )
        )

    sessions.sort(key=lambda s: (s.date, s.session_id), reverse=True)
    return sessions


def build_history_by_file(sessions: list[DevSession]) -> dict[str, list[DevSession]]:
    by_file: dict[str, list[DevSession]] = {}
    for sess in sessions:
        for fp in sess.target_files:
            by_file.setdefault(fp, []).append(sess)
    return by_file


def format_history_line(sess: DevSession) -> str:
    date = sess.date or sess.session_id
    next_part = sess.next_steps.strip() if sess.next_steps else "—"
    status = f" [{sess.status}]" if sess.status else ""
    return (
        f"{HISTORY_PREFIX} Modificato da {sess.agent_used} il {date}{status}. "
        f"Next: {next_part} _(log: {sess.source_file})_"
    )


def _file_line_pattern(path: str) -> re.Pattern[str]:
    esc = re.escape(path)
    return re.compile(rf"^-\s+{esc}:\s*(.*)$", re.MULTILINE)


def strip_existing_history(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith(HISTORY_PREFIX):
            i += 1
            continue
        out.append(line)
        i += 1
    return out


def inject_history(content: str, by_file: dict[str, list[DevSession]]) -> str:
    lines = strip_existing_history(content.splitlines())
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        result.append(line)
        matched_path: str | None = None
        m = re.match(r"^-\s+([^:]+):\s*(.*)$", line)
        if m:
            candidate = normalize_path(m.group(1).strip())
            if candidate in by_file:
                matched_path = candidate
        if matched_path:
            for sess in by_file[matched_path][:3]:
                result.append(format_history_line(sess))
        i += 1

    # File in sessioni ma assenti da ROOTPAM → sezione append
    mapped = set()
    for line in result:
        mm = re.match(r"^-\s+([^:]+):", line)
        if mm:
            mapped.add(normalize_path(mm.group(1).strip()))

    orphan = sorted(set(by_file) - mapped)
    if orphan:
        result.append("")
        result.append("## File toccati dalle IA (non ancora in mappa)")
        for fp in orphan:
            result.append(f"- {fp}: _(aggiungere descrizione in ROOTPAM)_")
            for sess in by_file[fp][:3]:
                result.append(format_history_line(sess))

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = "\n".join(result)
    text = re.sub(
        r"\*\*Ultimo aggiornamento router:\*\*.*",
        f"**Ultimo aggiornamento router:** {stamp}",
        text,
        count=1,
    )
    if not text.endswith("\n"):
        text += "\n"
    return text


def update_rootpam(*, dry_run: bool = False) -> int:
    sessions = load_sessions()
    by_file = build_history_by_file(sessions)

    if not ROOTPAM_PATH.is_file():
        print(f"ERRORE: {ROOTPAM_PATH} non trovato", file=sys.stderr)
        return 1

    original = ROOTPAM_PATH.read_text(encoding="utf-8", errors="replace")
    updated = inject_history(original, by_file)

    if dry_run:
        print(updated)
        return 0

    ROOTPAM_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print(
        f"ROOTPAM aggiornato: {len(sessions)} sessioni, "
        f"{len(by_file)} file con storico."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    dry = "--dry-run" in args
    return update_rootpam(dry_run=dry)


if __name__ == "__main__":
    raise SystemExit(main())
