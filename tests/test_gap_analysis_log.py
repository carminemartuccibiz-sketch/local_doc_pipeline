"""Gap analysis log_fn must not pass level= kwargs to UI wrapper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from workflows.gap_analysis import _gap_log, run_project_gap_analysis


def test_gap_log_warn_prefix_no_kwargs() -> None:
    messages: list[str] = []

    def log_fn(msg: str) -> None:
        messages.append(msg)

    _gap_log(log_fn, "Preflight fallito", level="WARN")
    assert messages == ["[WARN] Preflight fallito"]


def test_preflight_failure_does_not_pass_level_kwarg() -> None:
    messages: list[str] = []

    def log_fn(msg: str) -> None:
        messages.append(msg)

    with patch("workflows.gap_analysis.ingest_dir") as mock_ingest:
        mock_root = MagicMock()
        mock_root.is_dir.return_value = True
        doc = MagicMock()
        doc.is_file.return_value = True
        doc.name = "doc.md"
        mock_root.iterdir.return_value = [doc]
        mock_ingest.return_value = mock_root

        with patch("workflows.gap_analysis.memory_dir", return_value=MagicMock()):
            with patch("workflows.gap_analysis.set_gap_allm_memory_dir"):
                with patch("workflows.gap_analysis.PipelineSessionState"):
                    with patch("workflows.gap_analysis.gap_report_path") as mock_gap:
                        mock_gap.return_value.parent.mkdir = MagicMock()
                        with patch("workflows.gap_analysis.load_project", return_value={}):
                            with patch(
                                "workflows.gap_analysis.resolve_project_sot_paths",
                                return_value=[],
                            ):
                                with patch(
                                    "workflows.gap_analysis.open_gap_pipeline",
                                    return_value=None,
                                ):
                                    n = run_project_gap_analysis("demo", log_fn=log_fn)

    assert n == 0
    assert any("[WARN]" in m for m in messages)
    assert any("Preflight fallito" in m for m in messages)
