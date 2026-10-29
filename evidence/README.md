# Evidence

`evidence/live-e2e-redacted.json` is a redacted summary of seven real Nebius Token Factory runs through this gate, made on 2026-10-28 against commit `bed93b11`. Response ids, prompts, and model text were removed before this file was added. The unredacted originals are kept off this repository.

Every run in that file has `replay.reason` `ALREADY_CONSUMED`, `tamper.reason` `ACTION_MISMATCH`, and `verifier_valid: true` on every listed receipt.

`make live-e2e` (or `python -m gate_demo.live_e2e --out evidence/live-e2e.json`) repeats one live run on a machine where `NEBIUS_API_KEY` is set. That command writes `evidence/live-e2e.json`. Per-model runs can be grouped under `evidence/models/`. Both paths are listed in `.gitignore` so unredacted files are not committed. Commit another redacted summary only after you have checked that the key, prompts you do not want published, and raw model text are gone.

Do not rename `evidence/live-e2e.json` to bypass the ignore rule.
