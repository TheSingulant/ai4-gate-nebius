"""Live end-to-end run against Nebius Token Factory.

Requires ``NEBIUS_API_KEY`` in the environment. Does not print the key.
Writes a redacted evidence file (request ids, model, usage, timestamps, gate
decisions, receipt hashes, verifier results). This module does not invent
model output: if the call fails, the evidence status is ``error``.

    python -m gate_demo.live_e2e --out evidence/live-e2e.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from gate_demo.agent import Agent
from gate_demo.clock import rfc3339
from gate_demo.config import Settings, build_authorizer
from gate_demo.gate import Gate
from gate_demo.planner import LABEL_LIVE, NEBIUS_BASE_URL, NEBIUS_MODEL, make_planner
from gate_demo.safety import DailyBudget, evidence_json, secret_values
from gate_demo.workspace import Workspace
from gate_verify.verify import verify_receipt


def live_approver(proposal: dict[str, Any]) -> bool:
    """Unattended stand-in for a person. Approves only the coding-demo risky paths.

    The gate still binds the approval to the exact action hash. Anything else
    is declined. Policy denials never reach this function.
    """
    action = proposal["action"]
    tool, target = action["tool"], action["target"]
    if tool == "write_file" and target.startswith("scripts/") and target.endswith(".py"):
        return True
    if tool == "run_python" and target.startswith("scripts/") and target.endswith(".py"):
        return True
    if tool == "git_push" and target == "origin/main":
        return True
    return False


def _target(tool: str, args: dict[str, Any]) -> str:
    if tool == "git_push":
        return f"{args.get('remote')}/{args.get('branch')}"
    return str(args.get("path") or args.get("to") or tool)


def run_live(out_path: Path, env: Mapping[str, str] | None = None, *, max_steps: int = 16) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    key = source.get("NEBIUS_API_KEY", "")
    if not key:
        raise RuntimeError("BLOCKED: NEBIUS_API_KEY is not set. No live call was made.")
    source["PLANNER"] = "nebius"
    source.setdefault("NEBIUS_BASE_URL", NEBIUS_BASE_URL)
    source.setdefault("NEBIUS_MODEL", NEBIUS_MODEL)
    secrets = secret_values(source)
    settings = Settings.from_env(source)
    evidence: list[dict[str, Any]] = []
    budget = DailyBudget(settings.daily_request_budget, settings.daily_token_budget, ":memory:",
                         agent_run_cap=settings.daily_agent_runs)
    started = rfc3339(int(time.time()))
    workspace = Workspace.create(limits=settings.limits)
    report: dict[str, Any] = {
        "status": "error",
        "planner": LABEL_LIVE,
        "base_url": source["NEBIUS_BASE_URL"],
        "requested_model": source["NEBIUS_MODEL"],
        "started_at": started,
        "finished_at": None,
        "inference": [],
        "decisions": [],
        "receipts": [],
        "replay": {"status": "not_applicable"},
        "tamper": {"status": "not_applicable"},
        "verifier": {"checked": 0, "valid": 0, "invalid": 0},
        "note": "Redacted evidence from a live Nebius Token Factory run. The API key is not included.",
    }
    try:
        gate = Gate(build_authorizer(source), workspace, secrets=secrets)
        planner = make_planner(source, evidence=evidence, budget=budget, secrets=secrets)
        if getattr(planner, "label", "") != LABEL_LIVE:
            raise RuntimeError("refusing to run: planner is not NEMOTRON-LIVE")
        agent = Agent(gate, planner, approver=live_approver, approval_timeout=30, max_steps=max_steps)
        state = agent.run()
        keys = gate.keys()
        signer = keys["receipt_signer"]["public_key_hex"]
        approver = keys["approvers"][0]["public_key_hex"]
        by_id = {r.get("receipt_id"): r for r in gate.receipts}
        for obs in state.observations:
            receipt = by_id.get(obs.receipt_id) if obs.receipt_id else None
            report["decisions"].append({
                "tool": obs.call.tool,
                "target": _target(obs.call.tool, obs.call.args),
                "decision": obs.decision,
                "allowed": obs.allowed,
                "reason": obs.reason,
                "receipt_id": obs.receipt_id,
                "action_hash": None if receipt is None else receipt.get("action_hash"),
                "planner": None if receipt is None else receipt.get("planner"),
                "model_id": None if receipt is None else receipt.get("model_id"),
            })
        valid = 0
        for receipt in gate.receipts:
            result = verify_receipt(receipt, signer, approver)
            if result.valid:
                valid += 1
            report["receipts"].append({
                "receipt_id": receipt.get("receipt_id"),
                "action_hash": receipt.get("action_hash"),
                "decision": receipt.get("decision"),
                "reason": receipt.get("reason"),
                "planner": receipt.get("planner"),
                "model_id": receipt.get("model_id"),
                "issued_at": receipt.get("issued_at"),
                "verifier_valid": result.valid,
                "verifier_errors": result.errors,
            })
        report["verifier"] = {"checked": len(gate.receipts), "valid": valid, "invalid": len(gate.receipts) - valid}
        report["inference"] = list(evidence)
        approved = next((o for o in state.observations if o.allowed and o.proposal_id), None)
        if approved is not None:
            replay = gate.execute(approved.proposal_id, approved.call.tool, approved.call.args)
            replay_receipt = replay.get("receipt") or {}
            replay_check = verify_receipt(replay_receipt, signer, approver) if replay_receipt else None
            report["replay"] = {
                "status": "ran" if replay["allowed"] else "refused",
                "reason": replay["reason"],
                "action_hash": replay_receipt.get("action_hash"),
                "receipt_id": replay_receipt.get("receipt_id"),
                "verifier_valid": None if replay_check is None else replay_check.valid,
            }
            altered = dict(approved.call.args)
            if "content" in altered:
                altered["content"] = altered["content"] + "\n# tamper\n"
            elif "message" in altered:
                altered["message"] = altered["message"] + " tamper"
            elif "remote" in altered:
                altered["remote"] = "mirror"
            elif "path" in altered:
                altered["path"] = altered["path"] + ".tamper"
            else:
                altered = dict(approved.call.args)
            tamper = gate.execute(approved.proposal_id, approved.call.tool, altered)
            tamper_receipt = tamper.get("receipt") or {}
            tamper_check = verify_receipt(tamper_receipt, signer, approver) if tamper_receipt else None
            report["tamper"] = {
                "status": "ran" if tamper["allowed"] else "refused",
                "reason": tamper["reason"],
                "action_hash": tamper_receipt.get("action_hash"),
                "receipt_id": tamper_receipt.get("receipt_id"),
                "verifier_valid": None if tamper_check is None else tamper_check.valid,
            }
        errors = [event for event in gate.events if event.get("kind") == "planner_error"]
        if errors:
            report["status"] = "error"
            report["planner_errors"] = [event.get("error") for event in errors]
        elif report["verifier"]["invalid"]:
            report["status"] = "error"
        elif not report["inference"]:
            report["status"] = "error"
            report["planner_errors"] = ["no inference evidence was recorded"]
        else:
            report["status"] = "ok"
        return _finalize(report, out_path, secrets)
    except Exception as exc:
        report["status"] = "error"
        report["planner_errors"] = [str(exc)]
        report["inference"] = list(evidence)
        _finalize(report, out_path, secrets)
        raise
    finally:
        workspace.cleanup()


def _finalize(report: dict[str, Any], out_path: Path, secrets: list[str]) -> dict[str, Any]:
    report["finished_at"] = rfc3339(int(time.time()))
    text = evidence_json(report, secrets)
    for secret in secrets:
        if secret and secret in text:
            raise RuntimeError("refusing to write evidence: secret material survived redaction")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + "\n", encoding="utf-8")
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the live Nemotron gate flow and write redacted evidence.")
    parser.add_argument("--out", default="evidence/live-e2e.json")
    parser.add_argument("--max-steps", type=int, default=16)
    args = parser.parse_args(argv)
    if not os.environ.get("NEBIUS_API_KEY"):
        print("BLOCKED: NEBIUS_API_KEY is not set. No live call was made.", file=sys.stderr)
        print("Set the key in the environment, then run: make live-e2e", file=sys.stderr)
        return 2
    print(f"planner: NEMOTRON-LIVE")
    print(f"base_url: {os.environ.get('NEBIUS_BASE_URL', NEBIUS_BASE_URL)}")
    print(f"model: {os.environ.get('NEBIUS_MODEL', NEBIUS_MODEL)}")
    print("key: present (not printed)")
    try:
        report = run_live(Path(args.out), max_steps=args.max_steps)
    except Exception as exc:
        print(f"LIVE E2E FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"status: {report['status']}")
    print(f"inference calls: {len(report['inference'])}")
    print(f"receipts: {report['verifier']}")
    print(f"replay: {report['replay']}")
    print(f"tamper: {report['tamper']}")
    print(f"evidence: {args.out}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
