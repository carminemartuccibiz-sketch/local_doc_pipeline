"""Orchestratore sequenziale di workflow plugin (Flow YAML)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from engine.project_memory import load_flow_definition, load_flow_state, save_flow_state
from workflows.base_workflow import BaseWorkflow
from workflows.capabilities import WorkflowCapabilities


class FlowWorkflow(BaseWorkflow):
    capabilities = WorkflowCapabilities(
        requires_llm=True,
        requires_rag=False,
        supports_cancel=True,
    )

    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
        slug = ctx.get("slug")
        if not slug:
            raise ValueError("ctx['slug'] richiesto")
        stop_event = ctx.get("stop_event")
        log_fn: Callable[[str], None] = ctx.get("log_fn") or (lambda _m: None)
        flow_name = ctx.get("flow_name") or "default"

        from engine.workflow_runner import WorkflowRunner

        flow = load_flow_definition(slug, flow_name)
        fstate = load_flow_state(slug, flow_name)
        runner = WorkflowRunner()
        results: list[dict[str, Any]] = []

        for step in flow.get("steps") or []:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError(f"Flow {flow_name} interrotto")
            wf_name = str(step["workflow"])
            policy = step.get("on_error", "stop")
            retries = int(step.get("max_retries", 0))
            log_fn(f"[FLOW] Step {wf_name} su {file_path.name}")
            attempt = 0
            while True:
                attempt += 1
                try:
                    res = runner.run_file(wf_name, file_path, ctx)
                    results.append({"workflow": wf_name, "result": res})
                    fstate["last_step"] = wf_name
                    save_flow_state(slug, flow_name, fstate)
                    break
                except InterruptedError:
                    raise
                except Exception as e:
                    log_fn(f"[FLOW] Errore {wf_name}: {e}")
                    if attempt <= retries:
                        continue
                    if policy == "skip":
                        results.append({"workflow": wf_name, "error": str(e)})
                        break
                    raise

        return {"status": "ok", "flow": flow_name, "steps": results}
