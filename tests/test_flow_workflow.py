from pathlib import Path
from unittest.mock import MagicMock, patch

from workflows.flow import FlowWorkflow


def test_flow_workflow_runs_steps(tmp_path: Path) -> None:
    src = tmp_path / "x.py"
    src.write_text("print(1)", encoding="utf-8")
    wf = FlowWorkflow()
    flow_def = {"version": 1, "steps": [{"workflow": "test_workflow"}]}

    with patch("workflows.flow.load_flow_definition", return_value=flow_def):
        with patch("workflows.flow.load_flow_state", return_value={}):
            with patch("workflows.flow.save_flow_state"):
                with patch("engine.workflow_runner.WorkflowRunner") as mock_cls:
                    runner = MagicMock()
                    runner.run_file.return_value = {"status": "ok"}
                    mock_cls.return_value = runner
                    res = wf.process_file(
                        src,
                        {"slug": "demo", "flow_name": "test", "log_fn": lambda m: None},
                    )
    assert res["status"] == "ok"
    assert len(res["steps"]) == 1
