"""Delta sync AnythingLLM (Task B7 / MT-5.06)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_list_documents_parses_workspace_array() -> None:
    from clients.anythingllm import AnythingLLMClient

    client = AnythingLLMClient(base_url="http://test", api_key="k")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "workspace": [
            {
                "slug": "sot-ws",
                "documents": [
                    {
                        "title": "canon.md",
                        "docSource": "LAST DOCS/canon.md",
                        "docpath": "custom-documents/canon.md",
                    }
                ],
            }
        ]
    }
    with patch.object(client, "_http", return_value=mock_resp):
        docs = client.list_documents("sot-ws")
        keys = client.list_workspace_document_keys("sot-ws")
    assert len(docs) >= 1
    assert "LAST DOCS/canon.md" in keys
    assert "canon.md" in keys


def test_sot_in_workspace_matches_doc_source() -> None:
    from core.gap_allm import _sot_in_workspace

    remote = {"LAST DOCS/foo.md", "foo.md"}
    assert _sot_in_workspace("LAST DOCS/foo.md", Path("foo.md"), remote)
    assert not _sot_in_workspace("OTHER/bar.md", Path("bar.md"), remote)


def test_sync_skips_remote_on_force_delta(tmp_path: Path) -> None:
    from core.gap_allm import sync_sot_to_anythingllm

    sot = tmp_path / "canon.md"
    sot.write_text("# Canon\n", encoding="utf-8")
    files = [("LAST DOCS/canon.md", sot)]

    mock_client = MagicMock()
    mock_client.health.return_value = True
    mock_client.list_workspace_document_keys.return_value = {
        "LAST DOCS/canon.md",
        "canon.md",
    }
    mock_client.probe_vector_search.return_value = True

    state_file = tmp_path / "gap_allm_state.json"

    with (
        patch("core.gap_allm.GAP_USE_ALLM_RAG", True),
        patch("core.gap_allm._embed_mode", return_value="manual"),
        patch("core.gap_allm.AnythingLLMClient", return_value=mock_client),
        patch("core.gap_allm.resolve_sot_workspace_slug", return_value="dvamocles-sot"),
        patch("core.gap_allm.gap_allm_state_path", return_value=state_file),
    ):
        sync_sot_to_anythingllm(files, force=True)

    mock_client.upload_document.assert_not_called()
    mock_client.list_workspace_document_keys.assert_called_once_with("dvamocles-sot")


def test_sync_uploads_only_missing_remote(tmp_path: Path) -> None:
    from core.gap_allm import sync_sot_to_anythingllm

    existing = tmp_path / "existing.md"
    missing = tmp_path / "new.md"
    existing.write_text("# E\n", encoding="utf-8")
    missing.write_text("# N\n", encoding="utf-8")
    files = [
        ("LAST DOCS/existing.md", existing),
        ("LAST DOCS/new.md", missing),
    ]

    mock_client = MagicMock()
    mock_client.health.return_value = True
    mock_client.list_workspace_document_keys.return_value = {
        "LAST DOCS/existing.md",
        "existing.md",
    }
    mock_client.upload_document.return_value = ["loc-new.json"]
    mock_client.update_embeddings.return_value = None
    mock_client.probe_vector_search.return_value = True

    state_file = tmp_path / "gap_allm_state.json"

    with (
        patch("core.gap_allm.GAP_USE_ALLM_RAG", True),
        patch("core.gap_allm._embed_mode", return_value="per_file"),
        patch("core.gap_allm.AnythingLLMClient", return_value=mock_client),
        patch("core.gap_allm.resolve_sot_workspace_slug", return_value="dvamocles-sot"),
        patch("core.gap_allm.gap_allm_state_path", return_value=state_file),
    ):
        sync_sot_to_anythingllm(files, force=False)

    assert mock_client.upload_document.call_count == 1
    assert mock_client.upload_document.call_args[0][0] == missing
