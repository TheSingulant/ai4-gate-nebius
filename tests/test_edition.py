import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Split markers so a naive joined-form search still works as a guard.
# Private repository names are intentionally absent from this public tree;
# confidentiality is established by full-tree review, not by hashing names here.
FORBIDDEN_SUBSTRINGS = (
    "AI4_" + "ENDPOINT_URL",
    "AI4_" + "API_KEY",
    "AI4_" + "FALLBACK",
    "BEGIN " + "PRIVATE KEY",
    "\u2014",
    "\u2013",
)


def _text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".venv", "__pycache__", ".git", "demo-output"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".md", ".html", ".yml", ".yaml", ".json", ".toml", ".txt", ".example", ""}:
            if path.name not in {"Dockerfile", "Makefile", ".env.example", ".dockerignore", ".gitignore"}:
                continue
        yield path


def test_edition_has_no_private_adapter_or_dashes():
    offenders = []
    for path in _text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in FORBIDDEN_SUBSTRINGS:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {marker!r}")
    assert offenders == []


def test_no_authorizer_adapter_module():
    names = {path.name for path in (ROOT / "src/gate_demo/authorizers").glob("*.py")}
    assert "ai4.py" not in names
    assert "fallback.py" not in names


def test_raw_evidence_is_ignored_and_redacted_summary_is_present():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for line in ("evidence/live-e2e.json", "evidence/models/"):
        assert any(row.strip() == line for row in ignore.splitlines()), line
    summary = json.loads((ROOT / "evidence/live-e2e-redacted.json").read_text(encoding="utf-8"))
    runs = summary["runs"]
    assert len(runs) == 7
    models = [run["model"] for run in runs]
    assert models.count("nvidia/nemotron-3-super-120b-a12b") == 1
    assert models.count("nvidia/Nemotron-3.5-Super-VL") == 3
    assert models.count("nvidia/Nemotron-3_5-Lightning") == 3
    for run in runs:
        assert run["replay"]["reason"] == "ALREADY_CONSUMED"
        assert run["tamper"]["reason"] == "ACTION_MISMATCH"
        assert run["receipts_verified"]
        assert all(item["verifier_valid"] for item in run["receipts_verified"])


def test_scripted_demo_outcome(tmp_path):
    from gate_demo.demo import expected_outcome_ok, run_demo

    summary = run_demo(tmp_path / "out", env={"PLANNER": "scripted", "NEBIUS_API_KEY": "nf-test-sentinel-9f3a7c"}, quiet=True)
    assert expected_outcome_ok(summary)
    blob = json.dumps(summary)
    assert "nf-test-sentinel-9f3a7c" not in blob
    assert "NEMOTRON-LIVE" not in blob
