# Receipt format: `gate-receipt/1` (hackathon edition)

The gate produces one JSON receipt signed with Ed25519 for each execution-time decision (allowed or refused, including replay, tamper and missing-approval refusals) and for each outright policy denial at proposal time. A pending proposal that never executes and is not denied by policy does not generate a receipt, and an approval by itself is not a receipt. Anyone holding the signer's public key can check it offline with `verify.py`. A receipt proves which key signed which decision about which exact action, which planner produced it, and when. It does not prove that the tool ran, that it succeeded, or who the approver was.

The verifier in `src/gate_verify` does not import the gate. Both sides implement canonical JSON for the same restricted value space (RFC 8785 for that subset): UTF-8, sorted keys, no insignificant whitespace, integers only inside plus or minus (2^53 - 1), no floats.

## Action

```json
{"tool": "git_push", "args": {"remote": "origin", "branch": "main", "commit": "<40-hex sha>"}, "target": "origin/main"}
```

`action_hash` is the lowercase hex SHA-256 of the canonical action. Changing the tool, any argument, or the target changes the hash.

## Fields

| Field | Meaning |
|---|---|
| `receipt_version` | `gate-receipt/1` |
| `receipt_id` | 32 hex characters |
| `authorizer` | `reference` in this edition |
| `decision` | `allowed` or `refused` |
| `reason` | reason code below |
| `detail` | short explanation |
| `proposal_id` | proposal, or null |
| `action` | the action proposed or attempted |
| `action_hash` | SHA-256 of `action` |
| `approved_action_hash` | hash that was approved; differs on `ACTION_MISMATCH`; null if none |
| `approval` | approval object, or null |
| `issued_at` | RFC 3339 UTC, whole seconds, `Z` |
| `nonce` | 32 hex characters |
| `signer_key_id` | SHA-256 hex of the raw 32-byte Ed25519 public key |
| `planner` | `NEMOTRON-LIVE`, `SCRIPTED`, or `OPERATOR` |
| `model_id` | model id, or `scripted-offline`, or `none` |
| `signature` | base64 Ed25519 over the canonical receipt without `signature` |

`planner` and `model_id` are required in this edition and are covered by the signature. The earlier open demo receipt did not have these two fields. No other fields are allowed.

An approval signs:

```json
{"approval_version": "gate-approval/1", "approval_id": "...", "proposal_id": "...",
 "action_hash": "...", "approver_key_id": "...", "expires_at": "2026-10-28T00:00:00Z"}
```

## Reason codes

| Code | When |
|---|---|
| `POLICY_ALLOW` | policy allows it, first use |
| `APPROVED` | a valid unexpired approval for this exact action, first use |
| `POLICY_DENY` | policy denies it |
| `NOT_APPROVED` | a person is required and has not approved |
| `ACTION_MISMATCH` | the action differs from the proposal or the approval |
| `APPROVAL_SIGNATURE_INVALID` | the stored approval signature does not verify |
| `APPROVAL_EXPIRED` | the approval expiry has passed |
| `PROPOSAL_EXPIRED` | an auto-allowed proposal was not used in time |
| `ALREADY_CONSUMED` | replay of a proposal that already ran |
| `UNKNOWN_PROPOSAL` | no such proposal |

A refused attempt does not consume the proposal, except that a successful allow does. Replay after a successful allow is `ALREADY_CONSUMED`.

## Verifier

```bash
python verify.py receipt.json --trusted-key HEX [--approver-key HEX] [--json]
```

Exit 0 valid, 1 invalid, 2 input error. Checks cover the field set, the signer id, the signature, the action hash, the allowed-receipt binding, the planner label, and, when an approver key is supplied, the approval signature.
