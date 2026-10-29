"""Policy: ordered rules that map an action to allow, deny or needs_approval.

A policy file (YAML or JSON) looks like::

    version: 1
    default: deny
    rules:
      - tool: read_file
        decision: allow
      - tool: git_push
        target: origin/main
        decision: needs_approval
      - tool: git_push
        decision: deny
        reason: only origin/main may be pushed

Rules are checked in order and the first match wins. A rule matches when
``tool`` equals the action's tool (or is ``"*"``), ``target`` (a shell-style
glob, optional) matches the action's target, and every key in ``args``
(optional) equals the action's argument of the same name. If nothing matches,
``default`` applies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

ALLOW = "allow"
DENY = "deny"
NEEDS_APPROVAL = "needs_approval"
DECISIONS = (ALLOW, DENY, NEEDS_APPROVAL)

_RULE_KEYS = {"tool", "target", "args", "decision", "reason"}
_POLICY_KEYS = {"version", "default", "rules"}


class PolicyError(ValueError):
    """The policy document is malformed."""


@dataclass(frozen=True)
class Rule:
    tool: str
    decision: str
    target: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def matches(self, action: dict[str, Any]) -> bool:
        if self.tool != "*" and self.tool != action["tool"]:
            return False
        if self.target is not None and not fnmatchcase(action["target"], self.target):
            return False
        action_args = action["args"]
        return all(key in action_args and action_args[key] == value for key, value in self.args.items())


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    reason: str
    rule_index: int | None


class Policy:
    def __init__(self, rules: list[Rule], default: str = DENY) -> None:
        if default not in DECISIONS:
            raise PolicyError(f"default must be one of {DECISIONS}")
        self.rules = list(rules)
        self.default = default

    @classmethod
    def from_mapping(cls, data: Any) -> "Policy":
        if not isinstance(data, dict):
            raise PolicyError("policy must be a mapping")
        unknown = set(data) - _POLICY_KEYS
        if unknown:
            raise PolicyError(f"unknown policy keys: {sorted(unknown)}")
        if data.get("version") != 1:
            raise PolicyError("policy version must be 1")
        rules_data = data.get("rules", [])
        if not isinstance(rules_data, list):
            raise PolicyError("rules must be a list")
        rules = []
        for index, item in enumerate(rules_data):
            if not isinstance(item, dict):
                raise PolicyError(f"rule {index}: must be a mapping")
            unknown = set(item) - _RULE_KEYS
            if unknown:
                raise PolicyError(f"rule {index}: unknown keys {sorted(unknown)}")
            tool = item.get("tool")
            decision = item.get("decision")
            if not isinstance(tool, str) or not tool:
                raise PolicyError(f"rule {index}: tool is required")
            if decision not in DECISIONS:
                raise PolicyError(f"rule {index}: decision must be one of {DECISIONS}")
            target = item.get("target")
            if target is not None and not isinstance(target, str):
                raise PolicyError(f"rule {index}: target must be a string")
            args = item.get("args", {})
            if not isinstance(args, dict):
                raise PolicyError(f"rule {index}: args must be a mapping")
            reason = item.get("reason")
            if reason is not None and not isinstance(reason, str):
                raise PolicyError(f"rule {index}: reason must be a string")
            rules.append(Rule(tool=tool, decision=decision, target=target, args=dict(args), reason=reason))
        return cls(rules, default=data.get("default", DENY))

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            data = yaml.safe_load(text)
        else:
            raise PolicyError("policy file must end in .json, .yaml or .yml")
        return cls.from_mapping(data)

    def evaluate(self, action: dict[str, Any]) -> PolicyResult:
        for index, rule in enumerate(self.rules):
            if rule.matches(action):
                return PolicyResult(rule.decision, rule.reason or f"rule {index}: {rule.decision}", index)
        return PolicyResult(self.default, f"default: {self.default}", None)


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "policy" / "default.yaml"


def default_policy() -> Policy:
    """The policy shipped in ``policy/default.yaml`` (embedded copy if not found)."""
    if DEFAULT_POLICY_PATH.exists():
        return Policy.load(DEFAULT_POLICY_PATH)
    return Policy.from_mapping(EMBEDDED_DEFAULT)


EMBEDDED_DEFAULT: dict[str, Any] = {
    "version": 1,
    "default": DENY,
    "rules": [
        {"tool": "read_file", "decision": ALLOW},
        {"tool": "run_tests", "decision": ALLOW},
        {"tool": "write_file", "target": "src/*", "decision": ALLOW},
        {"tool": "write_file", "target": "tests/*", "decision": ALLOW},
        {"tool": "write_file", "decision": NEEDS_APPROVAL, "reason": "writes outside src/ and tests/ need a person"},
        {"tool": "run_python", "target": "scripts/*", "decision": NEEDS_APPROVAL, "reason": "running new code needs a person"},
        {"tool": "run_python", "decision": DENY, "reason": "only scripts/ may be run"},
        {"tool": "git_commit", "decision": ALLOW},
        {"tool": "git_push", "target": "origin/main", "decision": NEEDS_APPROVAL, "reason": "pushing to main needs a person"},
        {"tool": "git_push", "decision": DENY, "reason": "only origin/main may be pushed"},
        {"tool": "send_message", "args": {"to": "team@example.test"}, "decision": NEEDS_APPROVAL, "reason": "messages need a person"},
        {"tool": "send_message", "decision": DENY, "reason": "messages may only go to team@example.test"},
    ],
}
