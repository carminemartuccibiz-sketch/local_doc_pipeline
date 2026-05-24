"""
Workflow code review — sorgente in 01_INGEST → report Markdown in 03_OUTPUT/code_reviews/.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from core.ai_tasks import abort_if_stop_requested, llm_complete
from core.context_budget import truncate_middle
from core.converters import _read_text
from engine.project_memory import save_workflow_output
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.workflow_progress import report_llm_start, report_phase, report_save

WORKFLOW_ID = "code_analysis"
OUTPUT_SUBDIR = "code_reviews"

CODE_MAX_SOURCE_CHARS = int(os.environ.get("CODE_MAX_SOURCE_CHARS", "32000"))
CODE_LLM_MAX_OUTPUT = int(os.environ.get("CODE_LLM_MAX_OUTPUT", "2000"))
CODE_LLM_TEMPERATURE = float(os.environ.get("CODE_LLM_TEMPERATURE", "0.15"))

# Estensioni tipiche; altri file testuali vengono comunque letti con avviso in log.
_CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyw",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".scala",
        ".sh",
        ".bash",
        ".ps1",
        ".sql",
        ".vue",
        ".lua",
    }
)

_REQUIRED_SECTIONS = (
    "architettura",
    "vulnerabilit",
    "refactoring",
)

_SYSTEM_PROMPT = """\
Sei un senior software engineer e security reviewer (DevSecOps).
Analizzi codice sorgente (Python, JavaScript, TypeScript, Go, Rust, ecc.)
con focus su architettura, sicurezza e manutenibilità.

Produci un report in Markdown con ESATTAMENTE queste tre sezioni (titoli H2):

## Architettura generale
- Ruolo del file nel sistema, pattern principali, dipendenze rilevanti, integrazione.

## Vulnerabilità e debito tecnico
- Vulnerabilità potenziali (injection, secrets, concorrenza, gestione errori, logging sensibile).
- Debito tecnico e rischi operativi.

## Suggerimenti di refactoring
- Azioni concrete: estrazione funzioni, riduzione complessità, naming, separazione responsabilità, test.

Regole:
- Usa elenchi puntati per i punti principali.
- Cita pattern o simboli dal codice quando utile (nome funzione/classe).
- Non inventare file o moduli non presenti nel sorgente.
- Non aggiungere testo meta (es. "Ecco il report richiesto").
"""


def _guess_language(path: Path) -> str:
    ext = path.suffix.lower()
    mapping = {
        ".py": "Python",
        ".pyw": "Python",
        ".js": "JavaScript",
        ".mjs": "JavaScript",
        ".cjs": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".jsx": "JavaScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".kt": "Kotlin",
        ".cs": "C#",
        ".rb": "Ruby",
        ".php": "PHP",
        ".swift": "Swift",
        ".sh": "Shell",
        ".ps1": "PowerShell",
        ".sql": "SQL",
    }
    return mapping.get(ext, ext.lstrip(".") or "testo")


def _read_source_code(file_path: Path, log_fn: Callable[[str], None]) -> str:
    if not file_path.is_file():
        raise FileNotFoundError(f"File non trovato: {file_path}")

    size = file_path.stat().st_size
    if size == 0:
        raise ValueError(f"File sorgente vuoto: {file_path.name}")

    ext = file_path.suffix.lower()
    if ext and ext not in _CODE_EXTENSIONS:
        log_fn(
            f"[CODE] Estensione {ext!r} non standard — analisi testuale del contenuto"
        )

    code = _read_text(file_path)
    if not code.strip():
        raise ValueError(f"Nessun contenuto leggibile in {file_path.name}")

    line_count = code.count("\n") + 1
    log_fn(
        f"[CODE] Sorgente caricata: {file_path.name} "
        f"({size} byte, ~{line_count} righe, linguaggio {_guess_language(file_path)})"
    )
    return code


def _prepare_source_code(
    file_path: Path,
    code: str,
    log_fn: Callable[[str], None],
) -> str:
    if len(code) <= CODE_MAX_SOURCE_CHARS:
        return code
    truncated, was_truncated = truncate_middle(
        code,
        CODE_MAX_SOURCE_CHARS,
        file_path.name,
    )
    if was_truncated:
        log_fn(
            f"[CODE] Sorgente troncata a ~{CODE_MAX_SOURCE_CHARS} caratteri "
            f"(file grande: {file_path.name})"
        )
    return truncated


def _build_user_message(code: str, *, source_name: str, language: str) -> str:
    return (
        f"Analizza il file `{source_name}` ({language}) e produci il report nelle "
        "tre sezioni H2 richieste.\n\n"
        "CODICE SORGENTE (INIZIO)\n"
        "------------------------\n"
        f"```{language.lower()}\n{code}\n```\n"
        "------------------------\n"
        "CODICE SORGENTE (FINE)\n\n"
        "Produci solo il report Markdown con le tre sezioni."
    )


def _verify_report_sections(report_md: str, log_fn: Callable[[str], None]) -> None:
    lower = report_md.lower()
    missing = [label for label in _REQUIRED_SECTIONS if label not in lower]
    if missing:
        log_fn(
            "[CODE][WARN] Il report potrebbe essere incompleto — "
            f"sezioni non rilevate: {', '.join(missing)}"
        )
    else:
        log_fn("[CODE] Verifica sezioni report: Architettura, Vulnerabilità, Refactoring — OK")


class CodeAnalysisWorkflow(BaseWorkflow):
    capabilities = WorkflowCapabilities(
        requires_llm=True,
        requires_rag=False,
        supports_cancel=True,
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Scansiona sorgente in 01_INGEST, code review LLM, salva in 03_OUTPUT/code_reviews/.

        Log SSE con prefisso [CODE] e fasi numerate per tracciabilità UI.
        """
        slug = ctx.get("slug")
        if not slug:
            raise ValueError("ctx['slug'] richiesto")

        stop_event = ctx.get("stop_event")
        log_fn: Callable[[str], None] = ctx.get("log_fn") or (lambda _m: None)
        state = ctx.get("orchestrator")
        total_phases = 5

        def _check_stop(stage: str) -> None:
            if stop_event is not None and stop_event.is_set():
                log_fn(f"[CODE] Kill switch attivo {stage} — {file_path.name}")
                raise InterruptedError(f"CodeAnalysisWorkflow interrotto {stage}")

        _check_stop("all'avvio")
        report_phase(
            ctx,
            tag="CODE",
            phase=1,
            total=total_phases,
            label=f"Avvio code review su {file_path.name}",
            file_path=file_path,
        )

        report_phase(
            ctx,
            tag="CODE",
            phase=2,
            total=total_phases,
            label="Lettura e scansione sorgente",
            file_path=file_path,
        )
        try:
            raw_code = _read_source_code(file_path, log_fn)
        except (OSError, ValueError, FileNotFoundError) as e:
            log_fn(f"[CODE][ERROR] Lettura fallita: {e}")
            raise

        language = _guess_language(file_path)
        code = _prepare_source_code(file_path, raw_code, log_fn)

        report_phase(
            ctx,
            tag="CODE",
            phase=3,
            total=total_phases,
            label="Preparazione prompt (3 sezioni)",
            file_path=file_path,
        )
        user_message = _build_user_message(
            code,
            source_name=file_path.name,
            language=language,
        )

        _check_stop("prima della chiamata LLM")
        abort_if_stop_requested()
        report_llm_start(
            ctx,
            tag="CODE",
            phase=4,
            total=total_phases,
            file_path=file_path,
            detail=f"Code review LLM ({language})",
        )

        report_md = llm_complete(
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_message,
            temperature=CODE_LLM_TEMPERATURE,
            max_tokens=CODE_LLM_MAX_OUTPUT,
        ).strip()

        if not report_md:
            log_fn(f"[CODE][ERROR] LLM ha restituito report vuoto per {file_path.name}")
            raise RuntimeError(f"LLM ha restituito output vuoto per {file_path.name}")

        _check_stop("dopo la chiamata LLM")
        abort_if_stop_requested()
        _verify_report_sections(report_md, log_fn)

        report_save(
            ctx,
            tag="CODE",
            phase=5,
            total=total_phases,
            file_path=file_path,
            subdir=OUTPUT_SUBDIR,
        )
        out_name = f"{file_path.stem}.code_review.md"
        header = (
            f"# Code review — `{file_path.name}`\n\n"
            f"- **Linguaggio:** {language}\n"
            f"- **Workflow:** {WORKFLOW_ID}\n\n"
        )
        full_report = header + report_md.strip() + "\n"

        out_path = save_workflow_output(
            slug,
            OUTPUT_SUBDIR,
            out_name,
            full_report,
            source_file=file_path.name,
            state=state,
            bump_progress=False,
            current_file=file_path.name,
        )

        rel = f"{OUTPUT_SUBDIR}/{out_name}"
        log_fn(
            f"[CODE] Completato: {file_path.name} → {rel} "
            f"({out_path.stat().st_size} byte)"
        )

        return {
            "status": "ok",
            "workflow": WORKFLOW_ID,
            "source": file_path.name,
            "output": rel,
            "language": language,
        }
