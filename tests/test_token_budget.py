from core.token_budget import validate_request_budget


def test_validate_request_budget_overflow() -> None:
    r = validate_request_budget(
        model_id="llama-8k",
        system_prompt="x" * 100,
        user_prompt="y" * 50000,
        reserved_output=2048,
    )
    assert r["fits"] is False
    assert r["overflow"] > 0
