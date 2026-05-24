from pathlib import Path
from unittest.mock import patch

from workflows.doc_refactor import DocRefactorWorkflow, _parse_json_safe


def test_parse_json_safe_strips_fence() -> None:
    raw = '```json\n{"facts": []}\n```'
    data = _parse_json_safe(raw, 0)
    assert data.get("facts") == []


def test_doc_refactor_process_file_mock_llm(tmp_path: Path) -> None:
    src = tmp_path / "doc.md"
    src.write_text("# Hi\n\nSome content here.\n", encoding="utf-8")
    wf = DocRefactorWorkflow()
    calls = {"n": 0}

    def fake_llm(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"facts": [], "entities": [], "gaps_vs_sot": [], "open_questions": []}'
        return "# Gap Report\n\nSintesi test."

    with patch("workflows.doc_refactor.llm_complete", side_effect=fake_llm):
        with patch("workflows.doc_refactor.save_workflow_output") as mock_save:
            mock_save.return_value = tmp_path / "out.md"
            with patch(
                "workflows.doc_refactor.get_session_lm_model",
                return_value="test",
            ):
                res = wf.process_file(
                    src,
                    {"slug": "demo", "log_fn": lambda m: None},
                )
    assert res["status"] == "ok"
    assert calls["n"] >= 2
