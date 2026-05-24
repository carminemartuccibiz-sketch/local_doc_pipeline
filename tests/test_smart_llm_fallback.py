from unittest.mock import patch

from core.ai_tasks import smart_llm_complete


def test_smart_llm_fallback_second_model_succeeds() -> None:
    calls: list[int] = []

    def fake_unlocked(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("CUDA out of memory")
        return "ok"

    with patch("core.ai_tasks.parse_fallback_chain", return_value=["big", "small"]):
        with patch("core.ai_tasks._llm_complete_unlocked", side_effect=fake_unlocked):
            with patch(
                "core.ai_tasks.validate_request_budget",
                return_value={"fits": True, "usable": 1000, "projected": 100},
            ):
                with patch("core.ai_tasks.set_session_lm_model"):
                    with patch("core.ai_tasks.get_session_lm_model", return_value="big"):
                        with patch("core.ai_tasks.time.sleep"):
                            out = smart_llm_complete(
                                system_prompt="s",
                                user_message="u",
                            )
    assert out == "ok"
    assert len(calls) == 2
