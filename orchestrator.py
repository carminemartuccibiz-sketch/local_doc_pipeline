#!/usr/bin/env python3
"""
Orchestratore autonomo DVAMOCLES — ingest smart, gap analysis resilient, resume state.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config import DEFAULT_SOURCE_ROOT
from core.gap_runner import run_continuous_gap_pipeline, run_resilient_gap_pipeline
from core.ingest_copy import populate_raw_ingest
from core.paths import raw_ingest_dir, session_state_path
from core.preflight import LocalAIPreflightError, run_preflight_checks
from core.session_state import PipelineSessionState
from settings import GAP_BATCH_SIZE


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )


def run_autonomous_pipeline(
    *,
    repo_root: Path,
    skip_ingest: bool = False,
    skip_preflight: bool = False,
    limit: int | None = None,
    dry_run_ingest: bool = False,
    append_only: bool = True,
    skip_allm: bool = False,
    force_allm_sync: bool = False,
    reset_state: bool = False,
    continuous: bool = False,
    max_rounds: int | None = None,
) -> int:
    repo_root = repo_root.resolve()
    state = PipelineSessionState(session_state_path(repo_root))

    if reset_state:
        from core.reset_session import reset_gap_session

        for line in reset_gap_session(repo_root):
            print(f"  {line}")
        state = PipelineSessionState(session_state_path(repo_root))

    print("=" * 60)
    print("DVAMOCLES SWORD — Pipeline autonoma Gap Analysis")
    print("=" * 60)

    if not skip_preflight:
        print("\n[1/3] Pre-volo server locali...")
        try:
            run_preflight_checks(require_lm=True, require_allm=not skip_allm)
        except LocalAIPreflightError:
            return 2

    if not skip_ingest:
        print("\n[2/3] Smart ingestion -> 01_RAW_INGEST (deduplica MD5)...")
        result = populate_raw_ingest(repo_root=repo_root, dry_run=dry_run_ingest)
        state.record_ingest(
            copied=result.copied,
            skipped=result.skipped,
            errors=len(result.errors),
        )
        print(
            f"     Copiati: {result.copied} | Saltati: {result.skipped} | "
            f"Errori: {len(result.errors)}"
        )
        if result.errors:
            for err in result.errors[:5]:
                print(f"     ! {err}")
    else:
        print("\n[2/3] Ingest saltato (--skip-ingest)")

    print("\n[3/3] Gap Analysis (resume da pipeline_state.json)...")
    print(f"     State: {state.path}")
    print(f"     Ingest: {raw_ingest_dir(repo_root)}\n")

    try:
        if continuous:
            per_iter = max(1, limit or 1)
            n = run_continuous_gap_pipeline(
                repo_root=repo_root,
                append_only=append_only,
                skip_allm=skip_allm,
                force_allm_sync=force_allm_sync,
                state=state,
                files_per_iteration=per_iter,
                max_rounds=max_rounds,
            )
        else:
            n = run_resilient_gap_pipeline(
                repo_root=repo_root,
                limit=limit,
                append_only=append_only,
                skip_allm=skip_allm,
                force_allm_sync=force_allm_sync,
                state=state,
            )
    except RuntimeError as e:
        logging.error("%s", e)
        return 2
    except LocalAIPreflightError:
        return 2
    except KeyboardInterrupt:
        print("\n\nInterrotto dall'utente (Ctrl+C). Lo state e' salvato — rilancia per riprendere.")
        return 130

    if n == 0:
        print("\nNessun file processato (coda vuota o server non pronti).")
        return 0

    print(f"\nCompletato: {n} file analizzati in questa esecuzione.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Orchestratore autonomo DVAMOCLES (ingest + gap + resume)"
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--dry-run-ingest", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=GAP_BATCH_SIZE,
        help=f"File grezzi per esecuzione (default {GAP_BATCH_SIZE}, uno alla volta)",
    )
    parser.add_argument("--append-only", action="store_true")
    parser.add_argument("--skip-allm", action="store_true")
    parser.add_argument("--force-allm-sync", action="store_true")
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Azzera pipeline_state.json prima di partire",
    )
    parser.add_argument(
        "--continuous",
        "-c",
        action="store_true",
        help="Loop automatico: 1 file per iterazione fino a fine coda (usa con --limit 1)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="Con --continuous: stop dopo N iterazioni (default: illimitato)",
    )
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    return run_autonomous_pipeline(
        repo_root=args.source_root,
        skip_ingest=args.skip_ingest,
        skip_preflight=args.skip_preflight,
        limit=args.limit,
        dry_run_ingest=args.dry_run_ingest,
        append_only=args.append_only,
        skip_allm=args.skip_allm,
        force_allm_sync=args.force_allm_sync,
        reset_state=args.reset_state,
        continuous=args.continuous,
        max_rounds=args.max_rounds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
