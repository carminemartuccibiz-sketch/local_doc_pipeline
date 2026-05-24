"""
Workflow generazione blog post — documento tecnico → articolo Markdown stile Apple.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from core.ai_tasks import abort_if_stop_requested, llm_complete
from core.context_budget import truncate_middle
from engine.ingest_processor import IngestReadError, read_document_safe
from engine.project_memory import save_workflow_output
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities
from workflows.workflow_progress import report_llm_start, report_phase, report_save

WORKFLOW_ID = "blog_post"
OUTPUT_SUBDIR = "blog_posts"

BLOG_MAX_SOURCE_CHARS = int(os.environ.get("BLOG_MAX_SOURCE_CHARS", "28000"))
BLOG_LLM_MAX_OUTPUT = int(os.environ.get("BLOG_LLM_MAX_OUTPUT", "1600"))
BLOG_LLM_TEMPERATURE = float(os.environ.get("BLOG_LLM_TEMPERATURE", "0.2"))

_SYSTEM_PROMPT = """\
Sei un editor di contenuti per un blog tecnico moderno.
Scrivi articoli in stile Apple: minimalisti, chiari, focalizzati sui benefici per l'utente,
con titoli netti, sottotitoli brevi ed elenchi puntati essenziali.
Evita verbosità, gergo superfluo e frasi eccessivamente lunghe.
Tono professionale ma accessibile, con enfasi su chiarezza e leggibilità.

Regole di formattazione:
- Usa Markdown standard.
- Inizia con un titolo H1 chiaro.
- Usa H2/H3 per le sezioni principali.
- Usa elenchi puntati per benefici, feature o punti chiave.
- Paragrafi brevi (2–4 frasi).
- Non aggiungere preamboli meta (es. "Ecco l'articolo richiesto").
- Non inventare fatti non presenti nel documento sorgente.
"""


def _build_user_message(body: str, *, source_name: str) -> str:
    return (
        "Trasforma il seguente documento tecnico in un articolo da blog pronto per la "
        "pubblicazione, seguendo lo stile descritto nel system prompt.\n\n"
        f"**File sorgente:** `{source_name}`\n\n"
        "DOCUMENTO TECNICO (INIZIO)\n"
        "---------------------------\n"
        f"{body}\n"
        "---------------------------\n"
        "DOCUMENTO TECNICO (FINE)\n\n"
        "Produci solo il testo dell'articolo in formato Markdown."
    )


def _prepare_source_text(
    file_path: Path,
    log_fn: Callable[[str], None],
) -> str:
    body = read_document_safe(file_path, log_fn)
    if len(body) > BLOG_MAX_SOURCE_CHARS:
        body, truncated = truncate_middle(
            body,
            BLOG_MAX_SOURCE_CHARS,
            file_path.name,
        )
        if truncated:
            log_fn(
                f"[BLOG] Sorgente troncata a ~{BLOG_MAX_SOURCE_CHARS} caratteri "
                f"per {file_path.name}"
            )
    return body


class BlogPostWorkflow(BaseWorkflow):
    capabilities = WorkflowCapabilities(
        requires_llm=True,
        requires_rag=False,
        supports_cancel=True,
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Legge da 01_INGEST, genera articolo via LLM, salva in 03_OUTPUT/blog_posts/.

        ctx: slug (obbligatorio), stop_event, log_fn, orchestrator (opzionale).
        Il progresso UI è aggiornato da job_runner dopo process_file (bump_progress=False).
        """
        slug = ctx.get("slug")
        if not slug:
            raise ValueError("ctx['slug'] richiesto")

        stop_event = ctx.get("stop_event")
        log_fn: Callable[[str], None] = ctx.get("log_fn") or (lambda _m: None)
        state = ctx.get("orchestrator")

        def _check_stop(stage: str) -> None:
            if stop_event is not None and stop_event.is_set():
                log_fn(f"[BLOG] Kill switch attivo {stage} — {file_path.name}")
                raise InterruptedError(f"BlogPostWorkflow interrotto {stage}")

        total_phases = 3
        _check_stop("prima di iniziare")
        source_override = ctx.get("source_override")
        read_path = Path(source_override) if source_override else file_path
        report_phase(
            ctx,
            tag="BLOG",
            phase=1,
            total=total_phases,
            label=f"Lettura sorgente: {read_path.name}",
            file_path=file_path,
        )

        try:
            body = _prepare_source_text(read_path, log_fn)
        except IngestReadError as e:
            log_fn(f"[BLOG] Lettura fallita: {e}")
            raise

        user_message = _build_user_message(body, source_name=file_path.name)

        _check_stop("prima della chiamata LLM")
        abort_if_stop_requested()
        report_llm_start(
            ctx,
            tag="BLOG",
            phase=2,
            total=total_phases,
            file_path=file_path,
            detail="Generazione articolo LLM",
        )

        article_md = llm_complete(
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_message,
            temperature=BLOG_LLM_TEMPERATURE,
            max_tokens=BLOG_LLM_MAX_OUTPUT,
        ).strip()

        if not article_md:
            raise RuntimeError(f"LLM ha restituito output vuoto per {file_path.name}")

        _check_stop("dopo la chiamata LLM")
        abort_if_stop_requested()

        report_save(
            ctx,
            tag="BLOG",
            phase=3,
            total=total_phases,
            file_path=file_path,
            subdir=OUTPUT_SUBDIR,
        )
        out_name = f"{file_path.stem}.md"
        out_path = save_workflow_output(
            slug,
            OUTPUT_SUBDIR,
            out_name,
            article_md,
            source_file=file_path.name,
            state=state,
            bump_progress=False,
            current_file=file_path.name,
        )

        rel = f"{OUTPUT_SUBDIR}/{out_name}"
        log_fn(f"[BLOG] Salvato: {rel} ({out_path.stat().st_size} byte)")

        return {
            "status": "ok",
            "workflow": WORKFLOW_ID,
            "source": file_path.name,
            "output": rel,
        }
