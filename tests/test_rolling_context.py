from core.rolling_context import RollingContext


def test_trims_facts_to_max() -> None:
    rc = RollingContext(max_facts=20)
    for i in range(25):
        rc.add_chunk_result(
            {"facts": [{"claim": f"c{i}", "confidence": "high", "section": "S"}]},
            f"H{i}",
        )
    assert len(rc._facts) <= 20


def test_build_context_block_nonempty() -> None:
    rc = RollingContext()
    rc.add_chunk_result(
        {
            "facts": [
                {"claim": "Kill switch", "confidence": "high", "section": "Arch"}
            ]
        },
        "Architecture",
    )
    block = rc.build_context_block()
    assert "Kill switch" in block
    assert "Architecture" in block
