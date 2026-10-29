# Hackathon notes

Competition: Nebius x NVIDIA Global AI Hackathon, Coding and Agentic Engineering.
Deadline: Friday October 30, 2026, 10:00 AM PT.
Owner: The Singulant L.L.C-FZ.
Package: this directory, `ai4-gate-nebius-hackathon/`.

## What already existed on October 20, 2026

The private repository already had an open reference authorizer and a scripted coding demo. Those commits, on `main`, are:

- `457eacd` (2026-10-20) initial gate demo
- `8b0b7f3` (2026-10-20) fail-closed hosted authorizer path, labelled fallback, and the gated write, run, and test loop
- `aea66f7` (2026-10-20) merge of that work

That code already did exact-action SHA-256 binding, single-use approvals, Ed25519 receipts, an offline verifier, a scripted planner, and a Nebius planner client whose default model id was `nvidia/nemotron-3-super-120b-a12b` against `https://api.tokenfactory.us-central1.nebius.com/v1/`. The web UI refreshed about every 1.5 seconds and replaced the receipt list, which cleared a verify result.

This directory does not copy the hosted-authorizer client from that tree.

## What was built in this directory

Work on this edition is dated October 28, 2026, on branch `hackathon/nebius-edition`. It is a self-contained tree: its own package metadata, tests, Dockerfile, policy, verifier, and docs, so it can be copied into a new repository.

Material changes, compared with the October 20 demo:

- Release boundary. The edition ships only the open reference authorizer. There is no hosted-authority client and no fallback adapter.
- Planner defaults. Base URL `https://api.tokenfactory.nebius.com/v1/`. Model `nvidia/Nemotron-3.5-Super-VL`, because `nvidia/nemotron-3-super-120b-a12b` leaves Token Factory Serverless on November 9, 2026. Both are environment overrides. The scripted planner is labeled `SCRIPTED` and is not presented as Nemotron.
- Signed `planner` and `model_id` on every receipt, checked by the independent verifier.
- Hosted demo UI with task input, guided scenario, approve and deny, receipt hashes, and verify results kept in page state across the poll.
- Replay and tamper controls that show `ALREADY_CONSUMED` and `ACTION_MISMATCH`.
- Per-session sandbox with CPU, memory, time, output, and write limits. Local bare git remotes. Simulated outbox. No shell tool.
- Per-IP and global rate limits, plus daily request, token, and agent-run caps.
- Redaction so the Nebius key does not appear in receipts, logs, or API responses, with tests.
- `make live-e2e` writes a local evidence file that is gitignored. The checked-in summary is `evidence/live-e2e-redacted.json`. An opt-in `pytest -m live` test makes one real call when a key is present.
- Private preview settings: `PREVIEW_ACCESS_TOKEN`, security headers, trusted proxy handling, and a compose file with Caddy. See `docs/DEPLOY.md`. Not deployed.

## Live runs on October 28, 2026

Commit `bed93b11` was exercised on a machine that had `NEBIUS_API_KEY`. The edition tests there were reported as 32 passed, including the live test. The measured gate runs are summarized in `evidence/live-e2e-redacted.json`. Response ids, prompts, and model text are not in that file.

Seven runs, all with every listed receipt `verifier_valid` true, replay `ALREADY_CONSUMED`, and tamper `ACTION_MISMATCH`:

| Model | Runs | Actions | Prompt tokens | Completion tokens | Timestamp span |
|---|---|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | 1 | 10 | 20123 | 1379 | 15 seconds |
| `nvidia/Nemotron-3.5-Super-VL` | 3 | 9, 9, 9 (same tool sequence) | 16921, 16925, 16925 | 1131, 1134, 1171 | 10, 10, and 12 seconds |
| `nvidia/Nemotron-3_5-Lightning` | 3 | 10, 14, 13 | 22161, 32961, 29930 | 879, 1031, 953 | 9, 11, and 10 seconds |

The default is Super VL because that sequence was stable and the id is not on the November 9 serverless removal list. Lightning passed the gate checks but varied. One Lightning step proposed `mkdir`, which is not a tool. The gate recorded `unknown tool: 'mkdir'` and did not run it. The Super id above also passed, and it leaves serverless on November 9, 2026.

`make live-e2e` still writes `evidence/live-e2e.json` on a machine with a key. That path is gitignored. Do not commit it.
