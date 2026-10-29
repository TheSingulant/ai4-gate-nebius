# YouTube upload copy (draft, not uploaded)

Status: reference copy of the YouTube text. The live upload status is tracked on YouTube, not in this file.
Visibility when uploaded: Public (Devpost rules require a public YouTube video).
File: ai4-gate-nebius-demo-v2.mp4, about 1 minute 40 seconds, 1280x720, H.264 and AAC, captions burned in, synthetic narration, no music.

## Title (under 100 characters)

AI⁴ Gate: Permission Before Action | Nemotron on Nebius Token Factory

## Description

Capability is not permission. Every action leaves proof.

AI⁴ Gate puts an authorization gate between an NVIDIA Nemotron coding agent and its tools. Nemotron (nvidia/Nemotron-3.5-Super-VL on Nebius Token Factory) proposes one tool call at a time. Safe calls pass by policy, risky calls wait for a person to approve the exact action, and some calls are refused outright. An approval is bound to a SHA-256 hash of the exact action and works once. Change one byte and it is refused. Every execution-time decision, allowed or refused, and every outright policy denial at proposal time produces an Ed25519 signed receipt that an independent verifier checks offline.

What the video shows:
- Playback of captured live Nemotron runs (labeled LIVE): tool proposals, exact-action approval, replay refused (ALREADY_CONSUMED), tamper refused (ACTION_MISMATCH), and the offline verifier returning VALID, then INVALID on an edited copy.
- A scripted run, labeled SCRIPTED. It is not Nemotron.
- A screen capture of the Gate UI running locally with the scripted planner (labeled SCREEN CAPTURE, not Nemotron): exact-action approvals, signature verification, replay and tamper rejected.

Built for the Nebius x NVIDIA Global AI Hackathon, Coding and Agentic Engineering track.
Prototype by The Singulant L.L.C-FZ. Open source under Apache-2.0. The reference authorizer is not a production service.

Repository: https://github.com/TheSingulant/ai4-gate-nebius

Narration is synthetic speech. No music.

## Tags

AI agents, agent security, NVIDIA Nemotron, Nebius Token Factory, coding agent, tool calling, human in the loop, authorization, signed receipts, Ed25519, prompt injection, hackathon, open source

## Notes

- No credentials, hosted URL, IP address, or token appear in the video, title, description, or tags.
- Category suggestion: Science and Technology. Not made for kids. No paid promotion.
