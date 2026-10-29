"""The agent loop: ask the planner for a tool call, send it through the gate.

The agent never touches the workspace directly. For ``needs_approval`` it
either asks an ``approver`` callback (scripted demo) or waits for a person to
approve in the web UI.
"""

from __future__ import annotations

from typing import Any, Callable

from gate_demo.authorizers.base import DENY, NEEDS_APPROVAL, AuthorizerError
from gate_demo.gate import Gate
from gate_demo.planner import AgentState, Observation, ToolCall
from gate_demo.safety import BudgetExceeded
from gate_demo.tools import ToolError

Approver = Callable[[dict[str, Any]], bool]

DEFAULT_TASK = ("The test in tests/test_calc.py fails. Fix it, add a regression test, write and run a smoke "
                "script in scripts/, run the tests, commit, and push the fix to origin main.")


class Agent:
    def __init__(self, gate: Gate, planner: Any, *, approver: Approver | None = None,
                 approval_timeout: float = 300.0, max_steps: int = 25,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.gate = gate
        self.planner = planner
        self.approver = approver
        self.approval_timeout = approval_timeout
        self.max_steps = max_steps
        self.should_stop = should_stop

    def run(self, task: str = DEFAULT_TASK) -> AgentState:
        state = AgentState(task=task)
        label = getattr(self.planner, "label", "OPERATOR")
        model_id = getattr(self.planner, "model_id", "none")
        self.gate.log("task", text=task, planner=label, model_id=model_id)
        for _ in range(self.max_steps):
            if self.should_stop is not None and self.should_stop():
                self.gate.log("stopped", planner=label, model_id=model_id)
                break
            try:
                call = self.planner.next_call(state)
            except BudgetExceeded as exc:
                self.gate.log("budget", message=str(exc), planner=label, model_id=model_id)
                break
            except Exception as exc:
                self.gate.log("planner_error", error=str(exc), planner=label, model_id=model_id)
                break
            if call is None:
                break
            self.gate.log("plan", tool=call.tool, args=_preview(call.args), rationale=call.rationale,
                          planner=label, model_id=model_id)
            state.observations.append(self._step(call, label, model_id))
        self.gate.log("done", steps=len(state.observations), planner=label, model_id=model_id)
        return state

    def _step(self, call: ToolCall, planner: str, model_id: str) -> Observation:
        if call.reuse_proposal_id is not None:
            try:
                out = self.gate.execute(call.reuse_proposal_id, call.tool, call.args,
                                        planner=planner, model_id=model_id)
            except AuthorizerError as exc:
                return Observation(call, "reuse", False, exc.code, call.reuse_proposal_id, None, detail=str(exc))
            return Observation(call, "reuse", out["allowed"], out["reason"], call.reuse_proposal_id, out["result"],
                               receipt_id=_rid(out))
        try:
            proposal = self.gate.propose(call.tool, call.args, planner=planner, model_id=model_id)
        except ToolError as exc:
            self.gate.log("error", tool=call.tool, error=str(exc), planner=planner, model_id=model_id)
            return Observation(call, "invalid", False, str(exc), None, None)
        except AuthorizerError as exc:
            return Observation(call, "error", False, exc.code, None, None, detail=str(exc))
        pid = proposal["proposal_id"]
        if proposal["decision"] == DENY:
            code = (proposal.get("receipt") or {}).get("reason", "POLICY_DENY")
            return Observation(call, DENY, False, code, pid, None, detail=proposal["reason"], receipt_id=_rid(proposal))
        if proposal["decision"] == NEEDS_APPROVAL:
            if self.approver is not None:
                try:
                    if self.approver(proposal):
                        self.gate.approve(pid, proposal["action_hash"])
                    else:
                        self.gate.decline(pid)
                except AuthorizerError as exc:
                    return Observation(call, NEEDS_APPROVAL, False, exc.code, pid, None, detail=str(exc))
            outcome = self.gate.wait_for_decision(pid, self.approval_timeout)
            if outcome != "approved":
                return Observation(call, outcome, False, f"approval {outcome}", pid, None)
        try:
            out = self.gate.execute(pid, call.tool, call.args, planner=planner, model_id=model_id)
        except AuthorizerError as exc:
            return Observation(call, proposal["decision"], False, exc.code, pid, None, detail=str(exc))
        return Observation(call, proposal["decision"], out["allowed"], out["reason"], pid, out["result"],
                           detail=proposal["reason"], receipt_id=_rid(out))


def _rid(out: dict[str, Any]) -> str | None:
    return (out.get("receipt") or {}).get("receipt_id")


def _preview(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in args.items():
        text = value if isinstance(value, str) else str(value)
        out[key] = text if len(text) <= 120 else text[:117] + "..."
    return out
