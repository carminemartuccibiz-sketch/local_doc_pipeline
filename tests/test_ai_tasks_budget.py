from unittest.mock import patch


def test_truncate_user_for_context_uses_resolve_token_limits() -> None:
    from core import ai_tasks

    long_user = "word " * 50000
    with patch.object(ai_tasks, "count_tokens", return_value=100000):
        with patch.object(ai_tasks, "resolve_token_limits") as mock_limits:
            mock_limits.return_value.context_tokens = 8192
            mock_limits.return_value.response_reserve = 1500
            out = ai_tasks._truncate_user_for_context(
                model="test",
                system_prompt="sys",
                user_message=long_user,
                max_output_tokens=800,
            )
            assert len(out) < len(long_user)
