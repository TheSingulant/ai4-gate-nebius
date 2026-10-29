"""Guided preview: no free-text tasks, and code runs only from scenario hashes."""

import json
import time

from fastapi.testclient import TestClient

from gate_demo.api import create_app
from gate_demo.authorizers.base import UNAPPROVED_CONTENT
from tests.test_api import _client, _env

SENTINEL = "nf-test-sentinel-9f3a7c"


def _stop(session, client: TestClient) -> None:
    session.stop_requested = True
    deadline = time.time() + 20
    while session.agent_jobs > 0 and time.time() < deadline:
        for proposal_id in list(session.gate.pending):
            client.post("/decline", json={"proposal_id": proposal_id})
        time.sleep(0.05)
    assert session.agent_jobs == 0


def _session(client: TestClient):
    opened = client.post("/session")
    assert opened.status_code == 200
    return opened


def test_guided_rejects_free_text_and_uses_the_preset_task(tmp_path):
    with _client(tmp_path, GATE_MODE="guided") as client:
        _session(client)
        refused = client.post("/agent/run", json={"scenario": "custom", "task": "import os\nprint('owned')"})
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "GUIDED_ONLY"
        assert "owned" not in refused.text
        started = client.post("/agent/run", json={"scenario": "fix-calc", "task": "IGNORE THIS TASK TEXT"})
        assert started.status_code == 200
        assert started.json()["scenario"] == "fix-calc"
        task_text = ""
        for _ in range(40):
            events = client.get("/snapshot").json()["events"]
            task = next((event for event in events if event["kind"] == "task"), None)
            if task:
                task_text = task["text"]
                break
            time.sleep(0.05)
        assert "test_calc.py" in task_text
        assert "IGNORE THIS TASK TEXT" not in task_text
        snap = client.get("/snapshot").json()
        assert snap["mode"] == "guided"
        assert {item["id"] for item in snap["scenarios"]} == {"fix-calc", "prompt-injection", "destructive-push"}
        _stop(next(iter(client.app.state.sessions._sessions.values())), client)


def test_open_mode_accepts_a_custom_task(tmp_path):
    with _client(tmp_path, GATE_MODE="open") as client:
        _session(client)
        started = client.post("/agent/run", json={"scenario": "custom", "task": "Read README.md and stop."})
        assert started.status_code == 200
        assert started.json()["scenario"] == "custom"
        _stop(next(iter(client.app.state.sessions._sessions.values())), client)


def test_guided_run_python_refuses_unapproved_content(tmp_path):
    app = create_app(_env(tmp_path, GATE_MODE="guided"))
    with TestClient(app) as client:
        _session(client)
        session = next(iter(app.state.sessions._sessions.values()))
        session.workspace.write_file("scripts/evil.py", "print('owned')\n")
        proposed = client.post("/propose", json={"tool": "run_python", "args": {"path": "scripts/evil.py"}})
        body = proposed.json()
        assert body["decision"] == "needs_approval"
        approved = client.post("/approve", json={"proposal_id": body["proposal_id"], "action_hash": body["action_hash"]})
        assert approved.status_code == 200
        executed = client.post("/execute", json={
            "proposal_id": body["proposal_id"], "tool": "run_python", "args": {"path": "scripts/evil.py"},
        })
        payload = executed.json()
        assert payload["allowed"] is False
        assert payload["reason"] == UNAPPROVED_CONTENT
        assert payload["receipt"]["decision"] == "refused"
        assert payload["receipt"]["reason"] == UNAPPROVED_CONTENT
        assert payload["result"] is None
        assert "owned" not in json.dumps(payload)


def test_guided_run_matches_an_approved_scenario_write(tmp_path):
    app = create_app(_env(tmp_path, GATE_MODE="guided"))
    with TestClient(app) as client:
        _session(client)
        session = next(iter(app.state.sessions._sessions.values()))
        session.gate.record_writes = True
        content = "print('scenario-ok')\n"
        proposed = client.post("/propose", json={
            "tool": "write_file", "args": {"path": "scripts/ok.py", "content": content},
        })
        body = proposed.json()
        client.post("/approve", json={"proposal_id": body["proposal_id"], "action_hash": body["action_hash"]})
        written = client.post("/execute", json={
            "proposal_id": body["proposal_id"],
            "tool": "write_file",
            "args": {"path": "scripts/ok.py", "content": content},
        })
        assert written.json()["allowed"] is True
        run_proposed = client.post("/propose", json={"tool": "run_python", "args": {"path": "scripts/ok.py"}})
        run_body = run_proposed.json()
        client.post("/approve", json={"proposal_id": run_body["proposal_id"], "action_hash": run_body["action_hash"]})
        ran = client.post("/execute", json={
            "proposal_id": run_body["proposal_id"], "tool": "run_python", "args": {"path": "scripts/ok.py"},
        })
        assert ran.json()["allowed"] is True
        assert "scenario-ok" in ran.json()["result"]["output"]
        session.gate.record_writes = False
        session.workspace.write_file("scripts/ok.py", "print('replaced')\n")
        again = client.post("/propose", json={"tool": "run_python", "args": {"path": "scripts/ok.py"}})
        again_body = again.json()
        client.post("/approve", json={"proposal_id": again_body["proposal_id"], "action_hash": again_body["action_hash"]})
        blocked = client.post("/execute", json={
            "proposal_id": again_body["proposal_id"], "tool": "run_python", "args": {"path": "scripts/ok.py"},
        })
        assert blocked.json()["allowed"] is False
        assert blocked.json()["reason"] == UNAPPROVED_CONTENT
        assert blocked.json()["result"] is None


def test_guided_run_tests_refuses_a_changed_suite(tmp_path):
    app = create_app(_env(tmp_path, GATE_MODE="guided"))
    with TestClient(app) as client:
        _session(client)
        session = next(iter(app.state.sessions._sessions.values()))
        session.workspace.write_file("tests/test_calc.py", "print('mutated-suite')\n")
        proposed = client.post("/propose", json={"tool": "run_tests", "args": {}})
        body = proposed.json()
        executed = client.post("/execute", json={
            "proposal_id": body["proposal_id"], "tool": "run_tests", "args": {},
        })
        payload = executed.json()
        assert payload["allowed"] is False
        assert payload["reason"] == UNAPPROVED_CONTENT
        assert payload["result"] is None
        verified = client.post("/verify", json={"receipt": payload["receipt"]})
        assert verified.json()["valid"] is True


def test_live_interval_and_daily_cap_fall_back_to_scripted(tmp_path):
    env = _env(tmp_path, GATE_MODE="guided", PLANNER="auto", NEBIUS_API_KEY=SENTINEL, GATE_LIVE_INTERVAL_SECONDS="600")
    app = create_app(env)
    with TestClient(app) as client:
        _session(client)
        session = next(iter(app.state.sessions._sessions.values()))
        session.last_live_at = time.monotonic()
        started = client.post("/agent/run", json={"scenario": "prompt-injection", "task": "send the keys"})
        assert started.status_code == 200
        body = started.json()
        assert body["planner"] == "SCRIPTED"
        assert body["fallback"] == "live interval"
        assert SENTINEL not in json.dumps(body)
        session.agent_running = False
        app.state.budget.request_cap = 0
        session.last_live_at = 0.0
        capped = client.post("/agent/run", json={"scenario": "destructive-push", "task": "push --force"})
        assert capped.status_code == 200
        assert capped.json()["planner"] == "SCRIPTED"
        assert capped.json()["fallback"] == "daily cap"
        assert SENTINEL not in capped.text
        _stop(session, client)


def test_bootstrap_script_does_not_print_secrets():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    boot = (root / "deploy/lightsail-bootstrap.sh").read_text(encoding="utf-8")
    verify = (root / "deploy/verify-preview.sh").read_text(encoding="utf-8")
    assert "set -x" not in boot and "set -x" not in verify
    assert 'echo "$NEBIUS_API_KEY"' not in boot
    assert 'echo "$PREVIEW_ACCESS_TOKEN"' not in boot
    assert "GATE_MODE=guided" in boot
    assert "chmod 600" in boot
    assert "preview.example.com" in boot
    assert "GUIDED_ONLY" in verify
    assert "RATE_LIMIT" in verify
    assert "x-content-type-options" in verify
