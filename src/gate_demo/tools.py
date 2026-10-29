"""Tool registry: argument schemas, the action target, and how each tool runs.

An *action* is the exact thing the gate approves and hashes::

    {"tool": "git_push", "args": {"remote": "origin", "branch": "main", "commit": "<sha>"},
     "target": "origin/main"}

The gate computes ``target`` from ``args``; callers supply only tool and args.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from gate_demo.workspace import Workspace


class ToolError(ValueError):
    """The tool name or arguments are invalid."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: dict[str, str]  # argument name -> description (all strings)
    target: Callable[[dict[str, str]], str]
    run: Callable[[Workspace, dict[str, str]], dict[str, Any]]

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {name: {"type": "string", "description": desc} for name, desc in self.params.items()},
            "required": list(self.params),
            "additionalProperties": False,
        }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in [
        Tool("read_file", "Read a text file in the project.", {"path": "relative path"},
             lambda a: a["path"], lambda ws, a: ws.read_file(a["path"])),
        Tool("write_file", "Write a text file in the project.",
             {"path": "relative path", "content": "full new file content"},
             lambda a: a["path"], lambda ws, a: ws.write_file(a["path"], a["content"])),
        Tool("run_tests", "Run the project's unit tests.", {},
             lambda a: "tests", lambda ws, a: ws.run_tests()),
        Tool("run_python", "Run a Python file from the project and return its output.", {"path": "relative path"},
             lambda a: a["path"], lambda ws, a: ws.run_python(a["path"])),
        Tool("git_commit", "Commit all changes in the project.", {"message": "commit message"},
             lambda a: "HEAD", lambda ws, a: ws.git_commit(a["message"])),
        Tool("git_push", "Push one exact commit to a branch on a remote.",
             {"remote": "remote name", "branch": "branch name", "commit": "full commit sha"},
             lambda a: f"{a['remote']}/{a['branch']}",
             lambda ws, a: ws.git_push(a["remote"], a["branch"], a["commit"])),
        Tool("send_message", "Send a message (simulated; nothing leaves the machine).",
             {"to": "recipient address", "body": "message text"},
             lambda a: a["to"], lambda ws, a: ws.send_message(a["to"], a["body"])),
    ]
}


def build_action(tool: str, args: Any) -> dict[str, Any]:
    """Validate ``tool`` and ``args`` and return the canonical action object."""
    spec = TOOLS.get(tool) if isinstance(tool, str) else None
    if spec is None:
        raise ToolError(f"unknown tool: {tool!r}")
    if not isinstance(args, dict):
        raise ToolError("args must be an object")
    if set(args) != set(spec.params):
        raise ToolError(f"{tool}: args must be exactly {sorted(spec.params)}")
    for name, value in args.items():
        if not isinstance(value, str):
            raise ToolError(f"{tool}: argument {name} must be a string")
    clean = {name: args[name] for name in spec.params}
    return {"tool": tool, "args": clean, "target": spec.target(clean)}
