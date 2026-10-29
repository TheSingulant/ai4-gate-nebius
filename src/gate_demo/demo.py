"""Scripted end-to-end demo (offline).

    python -m gate_demo.demo --out demo-output

The scripted planner is labeled SCRIPTED. It is not NVIDIA Nemotron.
A live run is ``python -m gate_demo.live_e2e`` (see that module).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

from gate_demo.agent import Agent
from gate_demo.config import build_authorizer
from gate_demo.gate import Gate
from gate_demo.planner import LABEL_SCRIPTED, SCRIPTED_MODEL_ID, ScriptedPlanner, make_planner
from gate_demo.safety import secret_values
from gate_demo.workspace import Workspace
from gate_verify.verify import verify_receipt


APPROVE = {("write_file", "scripts/smoke.py"), ("run_python", "scripts/smoke.py"), ("git_push", "origin/main")}


def human_approver(proposal: dict[str, Any]) -> bool:
    """The scripted person: approves the smoke script write and run, and the push to origin/main."""
    action = proposal["action"]
    ok = (action["tool"], action["target"]) in APPROVE
    print(f"  [person] {'APPROVE' if ok else 'DECLINE'} {action['tool']} {action['target']} "
          f"(action_hash {proposal['action_hash'][:16]}...)")
    return ok


def run_demo(out_dir: Path, env: dict[str, str] | None = None, quiet: bool = False) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    env["PLANNER"] = "scripted"
    say = (lambda *a: None) if quiet else print
    workspace = Workspace.create()
    try:
        gate = Gate(build_authorizer(env), workspace, secrets=secret_values(env))
        planner = make_planner(env)
        agent = Agent(gate, planner, approver=human_approver, approval_timeout=5)
        say(f"planner: {planner.label} model: {planner.model_id}; authorizer: reference")
        state = agent.run()
        for i, obs in enumerate(state.observations, 1):
            code = (obs.result or {}).get("exit_code")
            say(f"  {i:2d}. {obs.call.tool:12s} {_target(obs):24s} {obs.decision:15s} "
                f"{'RAN' if obs.allowed else 'REFUSED'} {obs.reason}"
                + (f" exit={code}" if code is not None else "")
                + (f" ({obs.detail})" if obs.detail and not obs.allowed else "")
                + (f" receipt {obs.receipt_id[:8]}" if obs.receipt_id else " NO RECEIPT"))

        push = next((o for o in state.observations if o.call.tool == "git_push" and o.allowed), None)
        replay = None
        if push is not None:
            replay = gate.execute(push.proposal_id, "git_push", push.call.args)
            say(f"  replay of approved push: {'RAN' if replay['allowed'] else 'REFUSED'} {replay['reason']}")

        out_dir.mkdir(parents=True, exist_ok=True)
        receipts_dir = out_dir / "receipts"
        receipts_dir.mkdir(exist_ok=True)
        keys = gate.keys()
        (out_dir / "keys.json").write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        signer = keys["receipt_signer"]["public_key_hex"]
        approver = keys["approvers"][0]["public_key_hex"] if keys.get("approvers") else None

        verified = []
        for i, receipt in enumerate(gate.receipts, 1):
            path = receipts_dir / f"{i:02d}-{receipt['decision']}-{receipt['reason'].lower()}.json"
            path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            verified.append((path.name, verify_receipt(receipt, signer, approver).valid))

        tampered = None
        approved = next((r for r in gate.receipts if r["reason"] == "APPROVED"), None)
        if approved is not None:
            forged = copy.deepcopy(approved)
            forged["action"]["args"]["remote"] = "mirror"
            forged["action"]["target"] = "mirror/main"
            tampered_path = out_dir / "tampered-receipt.json"
            tampered_path.write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tampered = verify_receipt(forged, signer, approver)

        say(f"receipts: {len(verified)} written to {receipts_dir}; all valid: {all(v for _, v in verified)}")
        if tampered is not None:
            say(f"tampered copy: {'VALID (unexpected)' if tampered.valid else 'INVALID'}; "
                f"{'; '.join(tampered.errors)}")
        say(f"verify yourself: python verify.py {receipts_dir}/<file>.json --trusted-key {signer}")

        summary = {
            "planner": planner.name,
            "planner_label": planner.label,
            "model_id": planner.model_id,
            "authorizer": gate.authorizer.name,
            "steps": [{"tool": o.call.tool, "target": _target(o), "decision": o.decision, "allowed": o.allowed,
                       "reason": o.reason, "receipt_id": o.receipt_id,
                       "exit_code": (o.result or {}).get("exit_code")} for o in state.observations],
            "replay": None if replay is None else {"allowed": replay["allowed"], "reason": replay["reason"]},
            "receipts": [{"file": n, "valid": v} for n, v in verified],
            "receipt_labels": sorted({r.get("planner") for r in gate.receipts}),
            "receipt_models": sorted({r.get("model_id") for r in gate.receipts}),
            "tampered_valid": None if tampered is None else tampered.valid,
            "origin_main": workspace.remote_branch("origin", "main"),
            "mirror_main": workspace.remote_branch("mirror", "main"),
            "messages_sent": workspace.messages(),
            "trusted_key_hex": signer,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
    finally:
        workspace.cleanup()


def expected_outcome_ok(summary: dict[str, Any]) -> bool:
    """True when the scripted scenario produced the outcomes the demo claims."""
    if summary["planner"] != ScriptedPlanner.name:
        return False
    reasons = [(s["tool"], s["allowed"], s["reason"]) for s in summary["steps"]]
    want_push = ("git_push", True, "APPROVED")
    steps = {(s["tool"], s["target"], s["decision"], s["allowed"], s["reason"]) for s in summary["steps"]}
    tests = [s["exit_code"] for s in summary["steps"] if s["tool"] == "run_tests"]
    smoke = [s["exit_code"] for s in summary["steps"] if s["tool"] == "run_python"]
    receipt_ids = {r["receipt_id"] for r in summary["steps"]}
    return (
        summary["planner_label"] == LABEL_SCRIPTED
        and summary["model_id"] == SCRIPTED_MODEL_ID
        and summary["receipt_labels"] == [LABEL_SCRIPTED]
        and summary["receipt_models"] == [SCRIPTED_MODEL_ID]
        and ("write_file", "src/calc.py", "auto_allow", True, "POLICY_ALLOW") in steps
        and ("write_file", "tests/test_calc_edges.py", "auto_allow", True, "POLICY_ALLOW") in steps
        and ("write_file", "scripts/smoke.py", "needs_approval", True, "APPROVED") in steps
        and ("run_python", "scripts/smoke.py", "needs_approval", True, "APPROVED") in steps
        and smoke == [0] and len(tests) == 2 and tests[0] != 0 and tests[-1] == 0
        and None not in receipt_ids and len(receipt_ids) == len(summary["steps"])
        and want_push in reasons
        and ("git_push", False, "POLICY_DENY") in reasons
        and ("git_push", False, "ACTION_MISMATCH") in reasons
        and ("send_message", False, "POLICY_DENY") in reasons
        and summary["replay"] == {"allowed": False, "reason": "ALREADY_CONSUMED"}
        and all(r["valid"] for r in summary["receipts"])
        and summary["tampered_valid"] is False
        and summary["origin_main"] is not None
        and summary["mirror_main"] is None
        and summary["messages_sent"] == []
    )


def _target(obs: Any) -> str:
    args = obs.call.args
    if obs.call.tool == "git_push":
        return f"{args.get('remote')}/{args.get('branch')}"
    return args.get("path") or args.get("to") or "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scripted gate demo. This is not Nemotron.")
    parser.add_argument("--out", default="demo-output", help="directory for receipts and keys")
    args = parser.parse_args(argv)
    summary = run_demo(Path(args.out))
    ok = expected_outcome_ok(summary)
    print("DEMO OK" if ok else "DEMO FAILED: outcomes differ from the scripted scenario")
    print(f"planner label: {summary['planner_label']} model: {summary['model_id']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
