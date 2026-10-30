import pytest

from codecare.services.ai import HeuristicProvider, diagnose, get_provider, propose_fix


@pytest.mark.asyncio
async def test_heuristic_provider_used_without_keys():
    provider = get_provider()
    assert isinstance(provider, HeuristicProvider)
    name, text, is_ai = await diagnose("Memory leak", "connections not closed", "repo=demo")
    assert name == "heuristic"
    assert is_ai is False
    assert "not" in text.lower() or "unknown" in text.lower() or "heuristic" in text.lower()


@pytest.mark.asyncio
async def test_heuristic_does_not_invent_patches():
    name, summary, files, is_ai = await propose_fix("eval", "eval()", {"app.py": "eval(1)"})
    assert name == "heuristic"
    assert is_ai is False
    assert files == {}
    assert "not generate" in summary.lower() or "not" in summary.lower()
