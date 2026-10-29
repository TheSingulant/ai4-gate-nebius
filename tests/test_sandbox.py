import os
from pathlib import Path

from gate_demo.tools import TOOLS
from gate_demo.workspace import SandboxLimits, Workspace


def test_there_is_no_shell_tool():
    assert "shell" not in TOOLS
    assert "bash" not in TOOLS
    source = Path(__file__).resolve().parents[1].joinpath("src/gate_demo/workspace.py").read_text(encoding="utf-8")
    assert "shell=True" not in source


def test_python_timeout(tmp_path):
    limits = SandboxLimits(python_timeout=1, cpu_seconds=2, memory_bytes=256 * 1024 * 1024)
    ws = Workspace.create(tmp_path, limits)
    try:
        ws.write_file("scripts/sleep.py", "import time\ntime.sleep(30)\nprint('done')\n")
        result = ws.run_python("scripts/sleep.py")
        assert result["timed_out"] is True
        assert result["exit_code"] == 124
        assert "done" not in result["output"]
    finally:
        ws.cleanup()


def test_output_is_capped(tmp_path):
    limits = SandboxLimits(output_chars=80, python_timeout=8)
    ws = Workspace.create(tmp_path, limits)
    try:
        ws.write_file("scripts/chatty.py", "print('A' * 5000)\n")
        result = ws.run_python("scripts/chatty.py")
        assert len(result["output"]) <= 80
    finally:
        ws.cleanup()


def test_memory_limit(tmp_path):
    limits = SandboxLimits(memory_bytes=128 * 1024 * 1024, python_timeout=8, cpu_seconds=3)
    ws = Workspace.create(tmp_path, limits)
    try:
        ws.write_file("scripts/hog.py", "x = 'a' * (200 * 1024 * 1024)\nprint(len(x))\n")
        result = ws.run_python("scripts/hog.py")
        assert result["exit_code"] != 0
        assert "200000000" not in result["output"].replace(",", "")
    finally:
        ws.cleanup()


def test_child_environment_has_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "nf-test-sentinel-9f3a7c")
    ws = Workspace.create(tmp_path)
    try:
        ws.write_file("scripts/env.py", "import os\nprint(os.environ.get('NEBIUS_API_KEY', ''))\n")
        result = ws.run_python("scripts/env.py")
        assert "nf-test-sentinel-9f3a7c" not in result["output"]
        assert os.environ["NEBIUS_API_KEY"] not in result["output"]
    finally:
        ws.cleanup()


def test_write_limit_and_path_escape(tmp_path):
    limits = SandboxLimits(max_write_bytes=32)
    ws = Workspace.create(tmp_path, limits)
    try:
        try:
            ws.write_file("src/big.py", "x" * 100)
        except Exception as exc:
            assert "write limit" in str(exc)
        else:
            raise AssertionError("oversized write was accepted")
        try:
            ws.write_file("../outside.txt", "nope")
        except Exception as exc:
            assert "path" in str(exc).lower()
        else:
            raise AssertionError("path escape was accepted")
    finally:
        ws.cleanup()
