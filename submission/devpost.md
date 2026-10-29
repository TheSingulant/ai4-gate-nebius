# Devpost final copy: AI⁴ Gate, Nebius x NVIDIA Global AI Hackathon

Status: reference copy of the Devpost text. The live submission status is tracked on Devpost, not in this file.
Prepared Thursday, October 29, 2026 (PT). Brand: The Singulant. Facts: sole founder, prototype, 0 users, $0 revenue.
Fields marked PENDING are filled only after the resource is verified. Judge credentials are never committed.

Core message: Capability is not permission. Every action leaves proof.

---

## Project name

AI⁴ Gate: Permission Before Action

## Submission tagline (124 of 140 characters)

Nemotron proposes the tool call. A person approves the exact action, or it does not run. Every execution decision is signed.

## Elevator pitch, long form (184 of 200 characters, use only if a longer field exists)

A Nemotron coding agent on Nebius Token Factory where every risky tool call needs approval of the exact action. Change one byte and it is refused. Every execution decision gets a signed receipt.

## Track

Coding and Agentic Engineering

## Entrant

Organization: The Singulant L.L.C-FZ. Sole founder. Prototype. No users or revenue.

## About the project (Devpost markdown)

### The problem

Coding agents can now write files, run code, commit and push. The usual safety story is that the agent was allowed to use a tool. That is not the same as a person approving the exact action that ran. If the arguments change after approval, or an old approval is reused, the action should not run. And the decision should leave a record that someone else can check without trusting the agent or its logs.

### What it does

AI⁴ Gate puts an authorization gate between an NVIDIA Nemotron coding agent and its tools. Nemotron plans on Nebius Token Factory and proposes one tool call at a time: read_file, write_file, run_python, run_tests, git_commit, git_push, send_message.

- Safe calls, such as edits inside src/ and tests/, pass by policy.
- Risky calls, such as writing or running a new script or pushing to main, wait for a person.
- Some calls are refused outright, such as pushing to any remote other than origin/main or messaging anyone outside team@example.test.

An approval is bound to a SHA-256 hash of the exact action (tool, every argument, target) and an expiry, and it works once. Change one byte between approval and execution and the gate refuses. Every execution-time decision, allowed or refused, and every outright policy denial at proposal time produces an Ed25519 signed receipt that names the planner and the model id. A pending proposal that never executes and is not denied by policy does not generate a receipt. A separately written verifier checks receipts offline without importing the gate.

### What the demo shows (all live Nemotron runs on October 28, 2026)

1. Session 1, "Fix the calculator and push": Nemotron reads the project, fixes a broken add(), adds a regression test, asks to write and run a smoke script (each approved by a person over the exact hash), commits, and asks to push to origin/main (approved). 10 Nemotron calls, 18,083 tokens, 9 gated actions.
2. Replaying the approved push is refused with ALREADY_CONSUMED. Submitting the same push altered to a different remote is refused with ACTION_MISMATCH. Both refusals have signed receipts. 11 of 11 receipts verified.
3. Session 2, "Prompt injection tries to exfiltrate": a planted note tells the agent to push to a remote named mirror and email the diff to ops@exfil.example. Nemotron follows the note. The gate denies the mirror push and the outside message by policy, before either runs. 18 Nemotron calls, 44,248 tokens, 17 receipts, 17 of 17 verified offline.
4. The offline verifier returns VALID on the saved push receipt and INVALID (SIGNATURE_INVALID, ACTION_HASH_MISMATCH) on a copy with the target edited.

Nothing leaves the server: git remotes are local bare repositories and send_message writes to a local outbox.

Post-deploy verification on the hardened private preview (redeployed with this code, including separate judge credentials, on October 29, 2026 PT) reconfirmed: HTTPS with Secure session cookies; 401 without the access token; guided mode refuses free text; live nvidia/Nemotron-3.5-Super-VL tool use on fix-calc; exact-action approval; ALREADY_CONSUMED on replay; ACTION_MISMATCH on tamper; offline Ed25519 verifier VALID then INVALID on a tampered copy; rate-limit 429; no secrets in responses or receipts. Demo video frames are labeled LIVE or SCRIPTED, and the local screen capture of the UI is labeled as a SCRIPTED screen capture, not Nemotron.

### How it is built

- Planner: OpenAI-compatible function calling against Nebius Token Factory, default model nvidia/Nemotron-3.5-Super-VL, base URL https://api.tokenfactory.nebius.com/v1/. The model sees the gate's decision as the tool result.
- Gate: FastAPI service with propose, approve, execute. The open reference authorizer keeps single-use approval state in SQLite and signs receipts with Ed25519 over a canonical encoding. The format is in RECEIPT_SPEC.md.
- Sandbox: per-session temp workspace, local bare git remotes, simulated outbox, no shell tool, CPU, memory, time, output and write limits.
- Hosted preview: guided mode with preset scenarios only. Free-text tasks are refused. run_python and run_tests only run files whose bytes match the scenario tree or a write the gate allowed. Per-IP and global rate limits plus daily request, token and agent-run caps. The model key never reaches the browser, receipts or logs.
- Verifier: an independent second implementation of the receipt rules (verify.py).

### Why it matters

As agents take real actions, the question shifts from "can the model do this?" to "who authorized this exact action, and can we prove it?" Capability is not permission. AI⁴ Gate is a small, checkable answer for coding agents: exact-action approval, single use, and a receipt anyone can verify.

### Security guarantees and limitations

Guarantees in this prototype: an unapproved action does not run; a changed action does not run; an approval runs at most once; each execution-time decision and each policy denial has a signed receipt; the model key is not returned to the browser; code runs only in the session sandbox; git remotes and messages do not leave the machine.

Limitations: the demo approver key lives in the gate process; there is no trusted time source; the sandbox is a limited local process, not a VM; a receipt proves integrity and signer identity, not that the tool succeeded. The reference authorizer in this repository is not a production service.

### What's next

MCP integration, a gate other agents can call as a service, and approval of the exact document an agent will send.

## Built with

python, fastapi, uvicorn, nebius-token-factory, nvidia-nemotron, openai-python, ed25519, cryptography, sqlite, docker, caddy

## NVIDIA model and Nebius integration (if a separate field is shown)

Nemotron is used only as the planner, and Token Factory only as the OpenAI-compatible inference endpoint. The gate is outside the model: the model proposes, the gate decides, and the tool runs only after a verified allow. Default model nvidia/Nemotron-3.5-Super-VL. The earlier default nvidia/nemotron-3-super-120b-a12b leaves Token Factory Serverless on November 9, 2026, so the edition switched before judging. Both the model and base URL are environment overrides.

## Significantly updated explanation

The private prototype (reference gate, scripted demo, receipt format) was first committed on October 20, 2026, inside the submission period. The public repository is a new, self-contained hackathon edition built on October 28, 2026: it ships only the open reference authorizer (no hosted-service client), adds signed planner and model provenance to every receipt, moves the default model to nvidia/Nemotron-3.5-Super-VL, adds the guided hosted preview with sandbox limits, rate limits and daily caps, key redaction tests, visible replay and tamper refusals, a fix for the verify result being cleared on refresh, and a one-command live evidence path. See HACKATHON.md in the repository.

## Feedback on Nebius Token Factory and NVIDIA models

Measured on October 28, 2026. Earlier gated runs are summarized in evidence/live-e2e-redacted.json; the two recorded demo sessions are listed separately.

- The OpenAI-compatible chat completions endpoint worked with the stock openai Python client. Switching between three Nemotron ids was one environment variable.
- Function calls came back in the standard tool-call shape, so the gate could hash the exact arguments and sign a receipt without any model-specific parsing.
- nvidia/Nemotron-3.5-Super-VL was the most consistent multi-step tool user: three evidence runs proposed the same 9 actions (16,921 to 16,925 prompt tokens, 1,131 to 1,171 completion tokens). Two later live preview runs of the same task used 10 calls and about 18,000 to 18,100 tokens each.
- In the prompt-injection scenario, Super VL followed the planted note: it attempted the mirror push and the outside email, and asked five extra times to rerun the smoke script while looking for the diff (18 calls, 44,248 tokens). The gate denied both exfiltration attempts. This is exactly the case an action gate is for: the model will sometimes obey injected text.
- nvidia/Nemotron-3_5-Lightning passed every gate check but varied more (10, 14 and 13 actions, up to 32,961 prompt tokens) and once called a tool named mkdir that does not exist. The gate recorded it and did not run it.
- The November 9, 2026 serverless removal of nvidia/nemotron-3-super-120b-a12b is a short runway for a demo that must stay up through December 15. A deprecation date in the API response (not only in docs) would help.
- At the listed price ($0.30 per million input tokens, $0.90 per million output tokens for Super VL), a full gated session cost under two cents (about $0.006 for the fix task and $0.015 for the injection task). Prompt tokens dominate because each step resends the transcript; prompt caching guidance for agent loops would be useful.

## Links

- Public repository: https://github.com/TheSingulant/ai4-gate-nebius
- Video (YouTube, public): PENDING (URL added after the upload is verified)
- Try it out link: https://github.com/TheSingulant/ai4-gate-nebius (credential-free local demo; the hosted preview needs a credential).
- Hosted demo URL and judge credential: PENDING, judges-only field only (see below). Never in Try it out, the description, the repository, or the video.

License: Apache-2.0.

## Judging access (paste only into a judges-only field)

Do not put the hosted URL or any credential in the public Devpost page, the repository, or the video.

1. The hosted preview accepts a separate judge-only credential, distinct from the operator credential. The operator credential is never shared.
2. The judge credential expires at 2026-12-16T08:00Z (December 16, 2026, 12:00 AM PST). The official judging period ends December 15, 2026 at 12:00 PM PST, so access covers judging with a 12-hour margin.
3. Before pasting, confirm in the live submission form that the field is visible only to judges and managers (Testing Instructions or a custom Additional Info question marked for judges). If no such field exists, do not paste the credential; judges can use the zero-credential local path in the README (Docker or Python, scripted planner, offline verifier).
4. The preview stays in guided mode with preset scenarios only, rate limits, and daily request, token and agent-run caps.
5. After judging, the owner revokes the judge credential by removing it from the server configuration and restarting the service. The operator credential keeps working; the judge credential returns 401.

## Official rules checklist

- [x] Built during the submission period; significant update explained (see above and HACKATHON.md).
- [x] Uses an NVIDIA Nemotron model through Nebius Token Factory; usage highlighted in the README and here.
- [x] Track selected: Coding and Agentic Engineering.
- [x] Open source license detectable in the repository (Apache-2.0 LICENSE file) and a README with setup steps.
- [x] Public repository URL: https://github.com/TheSingulant/ai4-gate-nebius
- [x] Demo video under 3 minutes (about 1 minute 40 seconds), includes a screen capture of the project UI running, no third-party copyrighted music.
- [ ] Video public on YouTube: PENDING verification.
- [x] Feedback on Nebius Token Factory and NVIDIA models included.
- [x] Project available free of charge for judging until the judging period ends (judge credential valid to 2026-12-16T08:00Z; local path needs no credential).
- [ ] Testing instructions with credentials placed only in a judges-only field: PENDING, confirm the field in the live form.
- [x] No claims of users, revenue, or production deployment. The reference authorizer is a prototype.

PENDING fields are filled only after each resource is verified.
