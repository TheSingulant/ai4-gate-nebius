import os

import pytest

from gate_demo.live_e2e import run_live
from gate_demo.planner import NEBIUS_BASE_URL, NEBIUS_MODEL, NebiusPlanner, ScriptedPlanner


def test_defaults_match_the_verified_token_factory_endpoint():
    assert NEBIUS_BASE_URL == "https://api.tokenfactory.nebius.com/v1/"
    assert NEBIUS_MODEL == "nvidia/Nemotron-3.5-Super-VL"
    assert ScriptedPlanner.label == "SCRIPTED"
    assert "NEMOTRON" not in ScriptedPlanner.label
    assert NebiusPlanner.label == "NEMOTRON-LIVE"


def test_live_e2e_is_blocked_without_a_key(tmp_path):
    with pytest.raises(RuntimeError, match="BLOCKED"):
        run_live(tmp_path / "evidence.json", env={"PLANNER": "nebius"})
    assert not (tmp_path / "evidence.json").exists()


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("NEBIUS_API_KEY"), reason="NEBIUS_API_KEY not set")
def test_live_nemotron_tool_call(tmp_path):
    """One real Token Factory call. Skipped unless NEBIUS_API_KEY is set. Does not print the key."""
    from gate_demo.planner import AgentState
    from gate_demo.safety import DailyBudget, secret_values

    key = os.environ["NEBIUS_API_KEY"]
    evidence = []
    budget = DailyBudget(4, 50_000, ":memory:")
    planner = NebiusPlanner(key, evidence=evidence, budget=budget, secrets=secret_values(os.environ))
    state = AgentState("Call read_file on README.md exactly once, then stop.")
    planner.next_call(state)
    assert evidence, "live call returned no evidence"
    record = evidence[0]
    assert record["response_id"]
    assert record["usage"]["total_tokens"] > 0
    assert record["requested_model"] == os.environ.get("NEBIUS_MODEL", NEBIUS_MODEL)
    blob = __import__("json").dumps(evidence)
    assert key not in blob
    assert key not in repr(planner)
    out = tmp_path / "one-call.json"
    out.write_text(blob + "\n", encoding="utf-8")
    assert key not in out.read_text(encoding="utf-8")
