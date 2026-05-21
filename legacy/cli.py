#!/usr/bin/env python3
"""
CLI unificata DVAMOCLES — pipeline documentale + gap analysis.

  python cli.py check
  python cli.py init-ingest [--dry-run]
  python cli.py pipeline --step all --limit 2
  python cli.py ai-gap-analysis --target-path <file|dir> [opzioni]
  python cli.py run [--limit N]   # pipeline autonoma (ingest + gap + resume)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config import ANYTHINGLLM_API_KEY, DEFAULT_SOURCE_ROOT
from core.gap_runner import run_gap_analysis_loop
from core.ingest_copy import populate_raw_ingest
from core.paths import default_sot_directories, gap_report_path, raw_ingest_dir


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_check() -> int:
    from config import ANYTHINGLLM_API_V1_URL, LM_OPENAI_BASE_URL
    from core.ai_tasks import discover_lm_studio_model
    from core.preflight import LocalAIPreflightError, ping_anythingllm, ping_lm_studio

    from config import (
        GAP_BATCH_SIZE,
        PIPELINE_HARDWARE_PROFILE,
        PIPELINE_MAX_CONCURRENCY,
    )

    print("Pre-volo endpoint locali DVAMOCLES:\n")
    prof = PIPELINE_HARDWARE_PROFILE or "(non impostato)"
    print(
        f"  Profilo HW:        {prof}\n"
        f"  Concorrenza LLM:   {PIPELINE_MAX_CONCURRENCY}\n"
        f"  File per run:      {GAP_BATCH_SIZE}\n"
    )
    print(f"  LM Studio API:     {LM_OPENAI_BASE_URL}")
    print(f"  AnythingLLM API:   {ANYTHINGLLM_API_V1_URL}\n")

    lm_ok, lm_detail = ping_lm_studio()
    print(f"LM Studio:     {'OK' if lm_ok else 'FAIL'} — {lm_detail}")

    allm_ok, allm_detail = ping_anythingllm()
    key_note = "API key impostata" if ANYTHINGLLM_API_KEY else "API key MANCANTE"
    print(f"AnythingLLM:   {'OK' if allm_ok else 'FAIL'} ({key_note}) — {allm_detail}")

    if lm_ok:
        try:
            model = discover_lm_studio_model(force_refresh=True)
            print(f"  Modello attivo:  {model}")
        except RuntimeError as e:
            print(f"  Modello attivo:  FAIL — {e}")
            lm_ok = False

    if allm_ok and ANYTHINGLLM_API_KEY:
        try:
            from clients.anythingllm import AnythingLLMClient

            ws = AnythingLLMClient().list_workspaces()
            print(f"  Workspaces:      {len(ws)}")
        except Exception as e:
            print(f"  Workspaces:      errore ({e})")

    if not lm_ok or not allm_ok or not ANYTHINGLLM_API_KEY:
        print(
            "\nERRORE: Server LLM locali non raggiungibili. "
            "Assicurati di aver avviato il server in LM Studio e AnythingLLM prima di procedere."
        )
        return 2
    return 0


def cmd_init_ingest(args: argparse.Namespace) -> int:
    repo = args.source_root.resolve()
    dest = Path(args.dest).resolve() if args.dest else raw_ingest_dir(repo)
    result = populate_raw_ingest(
        repo_root=repo,
        ingest_root=dest,
        dry_run=args.dry_run,
    )
    print(
        f"\nIngest: copiati={result.copied} skipped={result.skipped} "
        f"errori={len(result.errors)} -> {dest}"
    )
    if result.errors:
        for e in result.errors[:10]:
            print(f"  ! {e}")
    return 1 if result.errors else 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    from legacy.pipeline import run_pipeline

    include = list(args.include or [])
    if args.priority_only:
        include.extend(
            [
                "refactor fatto/**/*.md",
                "LAST DOCS/**/*.md",
                "refactoring documentale raw/**/*.md",
            ]
        )
    run_pipeline(
        source_root=args.source_root.resolve(),
        steps={args.step} if args.step != "all" else {"all"},
        limit=args.limit,
        force=args.force,
        include_globs=include or None,
        skip_allm=args.skip_allm,
        skip_lm=args.skip_lm,
        dry_run=args.dry_run,
    )
    return 0


def cmd_reset_gap(args: argparse.Namespace) -> int:
    from core.reset_session import reset_gap_session

    repo = args.source_root.resolve()
    print("Reset sessione gap analysis:\n")
    for line in reset_gap_session(
        repo,
        requeue_all_pending=not args.wipe_state,
        keep_allm_cache=args.keep_allm_cache,
    ):
        print(f"  • {line}")
    print(
        "\nProssimo passo:\n"
        "  1. Se hai già «Salva e incorpora» in AnythingLLM: usa reset-gap --keep-allm-cache\n"
        "  2. In AnythingLLM: workspace SOT → elimina duplicati se hai rifatto upload API\n"
        "  3. LM Studio: Stop Server → ricarica modello → Start Server\n"
        "  4. .\\start_dvamocles_pipeline.ps1 -Limit 3   (test breve)\n"
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from legacy.orchestrator_v1 import run_autonomous_pipeline

    return run_autonomous_pipeline(
        repo_root=args.source_root.resolve(),
        skip_ingest=args.skip_ingest,
        skip_preflight=args.skip_preflight,
        limit=args.limit,
        dry_run_ingest=args.dry_run_ingest,
        append_only=args.append_only,
        skip_allm=args.skip_allm,
        force_allm_sync=args.force_allm_sync,
        reset_state=args.reset_state,
        continuous=getattr(args, "continuous", False),
        max_rounds=getattr(args, "max_rounds", None),
    )


def cmd_ai_gap_analysis(args: argparse.Namespace) -> int:
    repo = args.source_root.resolve()
    sot_paths = [Path(p).resolve() for p in (args.sot_path or [])]
    if not sot_paths:
        sot_paths = default_sot_directories(repo)
        logging.info(
            "SOT default (tier 1->2): %s",
            ", ".join(p.name for p in sot_paths),
        )

    target = Path(args.target_path).resolve()
    if not target.exists():
        logging.error("target-path non esiste: %s", target)
        return 1

    report = (
        Path(args.report_file).resolve()
        if args.report_file
        else gap_report_path(repo)
    )

    integrate = not args.append_only
    if args.append_only:
        logging.info("Modalita append-only (nessuna integrazione LLM del report cumulativo)")

    try:
        n = run_gap_analysis_loop(
            repo_root=repo,
            sot_paths=sot_paths,
            target_path=target,
            report_file=report,
            integrate=integrate,
            append_only=args.append_only,
            limit=args.limit,
            max_sot_chars=args.max_sot_chars,
            max_raw_chars=args.max_raw_chars,
            skip_allm=args.skip_allm,
            force_allm_sync=args.force_allm_sync,
        )
    except RuntimeError as e:
        logging.error("%s", e)
        return 2

    if n == 0:
        return 2

    print(f"\nGap analysis: {n} file processati.")
    print(f"Report cumulativo: {report}")
    print(f"Report per file:   {report.parent}/GAP_*.md")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="DVAMOCLES local doc CLI - pipeline + gap analysis SOT"
    )
    p.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help="Radice repo documentazione",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Verifica AnythingLLM + LM Studio + API key")

    init = sub.add_parser(
        "init-ingest",
        help="Copia sicura sorgenti grezzi in 01_RAW_INGEST (non elimina originali)",
    )
    init.add_argument(
        "--dest",
        default=None,
        help="Cartella destinazione (default: tools/local_doc_pipeline/01_RAW_INGEST)",
    )
    init.add_argument("--dry-run", action="store_true", help="Mostra cosa verrebbe copiato")

    pl = sub.add_parser("pipeline", help="Pipeline ingest/embed/extract")
    pl.add_argument("--step", choices=("convert", "embed", "extract", "all"), default="all")
    pl.add_argument("--limit", type=int, default=None)
    pl.add_argument("--force", action="store_true")
    pl.add_argument("--dry-run", action="store_true")
    pl.add_argument("--skip-allm", action="store_true")
    pl.add_argument("--skip-lm", action="store_true")
    pl.add_argument("--priority-only", action="store_true")
    pl.add_argument("--include", action="append", default=None)

    gap = sub.add_parser(
        "ai-gap-analysis",
        help="Confronto 1:1 grezzo vs SOT -> Gap Report incrementale",
    )
    gap.add_argument(
        "--sot-path",
        action="append",
        default=None,
        help="Cartella/file SOT (ripetibile). Default: LAST DOCS (tier 1) + Documentazione vecchia (tier 2)",
    )
    gap.add_argument(
        "--target-path",
        required=True,
        help="File o cartella grezza (es. 01_RAW_INGEST o path singolo)",
    )
    gap.add_argument(
        "--report-file",
        default=None,
        help="Report cumulativo (default: 02_SESSION_MEMORY/GAP_ANALYSIS_REPORTS/Gap_Report_Generale.md)",
    )
    gap.add_argument(
        "--append-only",
        action="store_true",
        help="Append sezioni al report cumulativo senza merge LLM",
    )
    gap.add_argument("--limit", type=int, default=None, help="Max file grezzi da analizzare")
    gap.add_argument(
        "--max-sot-chars",
        type=int,
        default=None,
        help="Budget caratteri contesto SOT (default 14000)",
    )
    gap.add_argument(
        "--max-raw-chars",
        type=int,
        default=None,
        help="Budget caratteri file grezzo (default 9000)",
    )
    gap.add_argument(
        "--skip-allm",
        action="store_true",
        help="Disabilita upload SOT e RAG su AnythingLLM",
    )
    gap.add_argument(
        "--force-allm-sync",
        action="store_true",
        help="Re-indicizza tutti i file SOT su AnythingLLM",
    )

    run = sub.add_parser(
        "run",
        help="Pipeline autonoma: ingest MD5 + gap analysis + resume state",
    )
    run.add_argument("--skip-ingest", action="store_true")
    run.add_argument("--skip-preflight", action="store_true")
    run.add_argument("--dry-run-ingest", action="store_true")
    from config import GAP_BATCH_SIZE

    run.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"Max file grezzi per run (default {GAP_BATCH_SIZE}, come orchestrator)",
    )
    run.add_argument("--append-only", action="store_true")
    run.add_argument("--skip-allm", action="store_true")
    run.add_argument("--force-allm-sync", action="store_true")
    run.add_argument("--reset-state", action="store_true")
    run.add_argument(
        "--continuous",
        "-c",
        action="store_true",
        help="Loop: 1 file per iterazione fino a fine coda",
    )
    run.add_argument("--max-rounds", type=int, default=None)

    reset_gap = sub.add_parser(
        "reset-gap",
        help="Reset state gap, report e cache upload SOT (mantiene 01_RAW_INGEST)",
    )
    reset_gap.add_argument(
        "--wipe-state",
        action="store_true",
        help="Elimina pipeline_state.json invece di rimettere tutti i file a pending",
    )
    reset_gap.add_argument(
        "--keep-allm-cache",
        action="store_true",
        help="Non cancellare gap_allm_state (SOT già incorporati in AnythingLLM)",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "check":
        return cmd_check()
    if args.command == "init-ingest":
        return cmd_init_ingest(args)
    if args.command == "pipeline":
        return cmd_pipeline(args)
    if args.command == "ai-gap-analysis":
        return cmd_ai_gap_analysis(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "reset-gap":
        return cmd_reset_gap(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
