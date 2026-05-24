"""Debug live: LM Studio + AnythingLLM + model router."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import httpx

from config import (
    ANYTHINGLLM_API_KEY,
    ANYTHINGLLM_BASE_URL,
    LM_MODEL,
    LM_OPENAI_BASE_URL,
)
from core.preflight import ping_anythingllm, ping_lm_studio, run_preflight_checks


def main() -> int:
    ok = True
    print("=== DEBUG LIVE — LM Studio + AnythingLLM ===\n")

    print(f"LM URL: {LM_OPENAI_BASE_URL}")
    lm_ok, lm_det = ping_lm_studio()
    print(f"LM Studio: {'OK' if lm_ok else 'FAIL'} — {lm_det}")
    ok &= lm_ok

    if lm_ok:
        url = LM_OPENAI_BASE_URL.rstrip("/") + "/models"
        r = httpx.get(url, timeout=15.0)
        data = r.json()
        models = [m.get("id") for m in data.get("data", [])[:10]]
        print(f"  Modelli ({len(data.get('data', []))} totali, primi 10): {models}")
        print(f"  LM_MODEL env: {LM_MODEL or '(auto-discovery)'}")

        try:
            from engine.model_router import get_model_router

            router = get_model_router()
            discovered = router.refresh()
            active = router.get_model_for_task("summary")
            print(f"  ModelRouter refresh: {len(discovered)} modelli, active(summary)={active!r}")
        except Exception as e:
            print(f"  ModelRouter: FAIL — {e}")
            ok = False

    print()
    print(f"ALLM URL: {ANYTHINGLLM_BASE_URL}")
    print(f"ALLM API key set: {bool(ANYTHINGLLM_API_KEY)}")
    allm_ok, allm_det = ping_anythingllm()
    print(f"AnythingLLM ping: {'OK' if allm_ok else 'FAIL'} — {allm_det}")
    ok &= allm_ok

    if allm_ok:
        try:
            from clients.anythingllm import AnythingLLMClient

            client = AnythingLLMClient()
            workspaces = client.list_workspaces()
            slugs = [
                w.get("slug") or w.get("name") or str(w)
                for w in workspaces[:8]
            ]
            print(f"  Workspaces ({len(workspaces)}): {slugs}")
            if workspaces:
                slug = workspaces[0].get("slug") or workspaces[0].get("name")
                if slug:
                    docs = client.list_documents(str(slug))
                    print(f"  Docs in {slug!r}: {len(docs)}")
                    probe = client.probe_vector_search(str(slug))
                    print(f"  Vector search probe ({slug!r}): {probe}")
        except Exception as e:
            print(f"  AnythingLLM API: FAIL — {type(e).__name__}: {e}")
            ok = False

    print()
    try:
        run_preflight_checks(require_lm=True, require_allm=True, exit_on_failure=False)
        print("run_preflight_checks: PASS")
    except Exception as e:
        print(f"run_preflight_checks: FAIL — {e}")
        ok = False

    if lm_ok and allm_ok:
        print()
        print("=== INTEGRATION SMOKE (LLM + gap session) ===")
        try:
            from core.ai_tasks import init_gap_analysis_session, llm_complete

            model = init_gap_analysis_session(require_allm=True, force_refresh=False)
            print(f"  init_gap_analysis_session: OK — model={model!r}")
            reply = llm_complete(
                system_prompt="Sei un assistente di test. Rispondi in una riga.",
                user_message="Rispondi solo: OK-LIVE",
                max_tokens=16,
            )
            snippet = (reply or "").strip().replace("\n", " ")[:120]
            print(f"  llm_complete: OK — risposta={snippet!r}")
        except Exception as e:
            print(f"  integration smoke: FAIL — {type(e).__name__}: {e}")
            ok = False

    print()
    print("Esito debug live:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
