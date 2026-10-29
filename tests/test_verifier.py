import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

from gate_demo.canonical import canonical_bytes
from gate_demo.tools import build_action
from gate_verify.verify import canonical, verify_receipt

ROOT = Path(__file__).resolve().parents[1]
PUSH = build_action("git_push", {"remote": "origin", "branch": "main", "commit": "cd" * 20})


def test_verifier_accepts_a_real_receipt(authorizer, signer_hex, approver_hex):
    proposal = authorizer.propose(PUSH, planner="SCRIPTED", model_id="scripted-offline")
    authorizer.approve(proposal.proposal_id, proposal.action_hash)
    receipt = authorizer.authorize_execution(proposal.proposal_id, PUSH).receipt
    result = verify_receipt(receipt, signer_hex, approver_hex)
    assert result.valid, result.errors
    assert result.summary["planner"] == "SCRIPTED"
    assert result.summary["model_id"] == "scripted-offline"


def test_verifier_rejects_a_tampered_receipt(authorizer, signer_hex, approver_hex):
    proposal = authorizer.propose(PUSH, planner="OPERATOR", model_id="none")
    authorizer.approve(proposal.proposal_id, proposal.action_hash)
    receipt = authorizer.authorize_execution(proposal.proposal_id, PUSH).receipt
    forged = copy.deepcopy(receipt)
    forged["action"]["args"]["remote"] = "mirror"
    forged["action"]["target"] = "mirror/main"
    result = verify_receipt(forged, signer_hex, approver_hex)
    assert not result.valid
    assert any("ACTION_HASH_MISMATCH" in error or "SIGNATURE_INVALID" in error for error in result.errors)


def test_verifier_rejects_a_bad_signer(authorizer):
    proposal = authorizer.propose(PUSH)
    receipt = authorizer.authorize_execution(proposal.proposal_id, PUSH).receipt
    result = verify_receipt(receipt, "ab" * 32)
    assert not result.valid
    assert any("SIGNER_MISMATCH" in error for error in result.errors)


def test_cli_exit_codes(authorizer, signer_hex, tmp_path):
    proposal = authorizer.propose(PUSH, planner="SCRIPTED", model_id="scripted-offline")
    authorizer.approve(proposal.proposal_id, proposal.action_hash)
    receipt = authorizer.authorize_execution(proposal.proposal_id, PUSH).receipt
    assert receipt["decision"] == "allowed"
    good = tmp_path / "good.json"
    good.write_text(json.dumps(receipt), encoding="utf-8")
    forged = copy.deepcopy(receipt)
    forged["decision"] = "refused"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(forged), encoding="utf-8")
    run = lambda *args: subprocess.run([sys.executable, str(ROOT / "verify.py"), *args], capture_output=True, text=True)
    ok = run(str(good), "--trusted-key", signer_hex)
    assert ok.returncode == 0 and ok.stdout.startswith("VALID")
    assert run(str(bad), "--trusted-key", signer_hex).returncode == 1
    assert run(str(good), "--trusted-key", "zz").returncode == 2


def test_canonical_forms_match(authorizer):
    proposal = authorizer.propose(PUSH)
    receipt = authorizer.authorize_execution(proposal.proposal_id, PUSH).receipt
    assert canonical(receipt) == canonical_bytes(receipt)


def test_verifier_imports_nothing_from_the_gate():
    allowed = {"__future__", "argparse", "base64", "binascii", "hashlib", "json", "sys", "dataclasses",
               "typing", "cryptography"}
    for path in [ROOT / "src" / "gate_verify" / "verify.py", ROOT / "src" / "gate_verify" / "__init__.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert roots <= allowed, f"{path.name} imports {roots - allowed}"
