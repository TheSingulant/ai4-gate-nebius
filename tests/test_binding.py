"""Hash binding, replay, and tamper on the open reference authorizer."""

import copy

from gate_demo.authorizers.base import ACTION_MISMATCH, ALREADY_CONSUMED
from gate_demo.tools import build_action
from gate_verify.verify import verify_receipt

PUSH = build_action("git_push", {"remote": "origin", "branch": "main", "commit": "ab" * 20})


def test_approval_binds_the_exact_action_hash(authorizer, signer_hex, approver_hex):
    proposal = authorizer.propose(PUSH, planner="SCRIPTED", model_id="scripted-offline")
    authorizer.approve(proposal.proposal_id, proposal.action_hash)
    decision = authorizer.authorize_execution(proposal.proposal_id, PUSH)
    assert decision.allowed and decision.reason == "APPROVED"
    receipt = decision.receipt
    assert receipt["planner"] == "SCRIPTED"
    assert receipt["model_id"] == "scripted-offline"
    assert receipt["action_hash"] == proposal.action_hash
    assert verify_receipt(receipt, signer_hex, approver_hex).valid


def test_altered_action_is_rejected(authorizer, signer_hex):
    proposal = authorizer.propose(PUSH, planner="NEMOTRON-LIVE", model_id="nvidia/Nemotron-3.5-Super-VL")
    authorizer.approve(proposal.proposal_id, proposal.action_hash)
    altered = copy.deepcopy(PUSH)
    altered["args"] = dict(altered["args"])
    altered["args"]["remote"] = "mirror"
    altered["target"] = "mirror/main"
    decision = authorizer.authorize_execution(proposal.proposal_id, altered)
    assert not decision.allowed and decision.reason == ACTION_MISMATCH
    assert decision.receipt["planner"] == "NEMOTRON-LIVE"
    assert decision.receipt["model_id"] == "nvidia/Nemotron-3.5-Super-VL"
    assert verify_receipt(decision.receipt, signer_hex).valid


def test_replay_is_rejected(authorizer, signer_hex):
    proposal = authorizer.propose(PUSH, planner="SCRIPTED", model_id="scripted-offline")
    authorizer.approve(proposal.proposal_id, proposal.action_hash)
    first = authorizer.authorize_execution(proposal.proposal_id, PUSH)
    second = authorizer.authorize_execution(proposal.proposal_id, PUSH)
    assert first.allowed
    assert not second.allowed and second.reason == ALREADY_CONSUMED
    assert verify_receipt(second.receipt, signer_hex).valid


def test_approve_rejects_a_different_hash(authorizer):
    proposal = authorizer.propose(PUSH)
    try:
        authorizer.approve(proposal.proposal_id, "0" * 64)
    except Exception as exc:
        assert exc.code == ACTION_MISMATCH
    else:
        raise AssertionError("mismatched hash was approved")


def test_forged_approval_signature_is_rejected(authorizer):
    proposal = authorizer.propose(PUSH)
    authorizer.approve(proposal.proposal_id, proposal.action_hash)
    authorizer.tamper_approval_signature_for_tests(proposal.proposal_id)
    decision = authorizer.authorize_execution(proposal.proposal_id, PUSH)
    assert not decision.allowed and decision.reason == "APPROVAL_SIGNATURE_INVALID"
