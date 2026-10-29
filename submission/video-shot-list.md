# Video shot list

Target length: under 3 minutes. This is a shot list, not a recording.

Record the real UI. If the key is not loaded, show the SCRIPTED badge and say that the planner is not Nemotron. If the key is loaded, show the NEMOTRON-LIVE badge, the model id `nvidia/Nemotron-3.5-Super-VL`, and a receipt or `evidence/live-e2e-redacted.json`. Do not show the API key. Do not show a terminal command that prints it.

## 0:00 to 0:20, problem

A coding agent that can write files, run them, commit, and push needs a person to approve the exact action. An approval of "push" is not an approval of a different remote, a different commit, or the same push a second time.

## 0:20 to 0:45, AI4 Gate and Nemotron

Show the header. Point at the planner badge, the model id, and the authorizer badge (`reference`). Say that Nemotron on Nebius Token Factory proposes the tool call, and the gate decides before the tool runs. If the badge says SCRIPTED, say this pass is the offline planner, not the model.

## 0:45 to 1:40, live coding workflow through Nebius

Click Run guided scenario, or run `make live-e2e` and then show the UI of a live session. Show Nemotron-proposed actions appearing one at a time: read, test, write the fix, write a test. Show a safe action running without a prompt, and the receipt row with `NEMOTRON-LIVE` or `SCRIPTED` and the action hash. If this take is scripted, keep the SCRIPTED badge in frame so the recording does not imply a live model call.

## 1:40 to 2:15, approval, hash binding, tamper rejection

Stop on a needs-approval card (the smoke script, running it, or the push to origin/main). Show the hash. Click Approve this exact action. Then click Submit altered action. Show TAMPER REJECTED and `ACTION_MISMATCH`. Say the altered bytes never ran.

## 2:15 to 2:40, signed receipts and independent verification

Click Verify signature on an allowed receipt and on the mismatch receipt. Leave the result on screen for more than two seconds so the refresh does not look like it cleared it. Optionally show `python verify.py` on a saved receipt in a terminal, including the INVALID result on the tampered file. Show Replay last allowed action and REPLAY REJECTED / `ALREADY_CONSUMED`.

## 2:40 to 2:55, why it matters

One sentence: the model can propose work, and a person still approves the exact call, with a receipt anyone can check. Say this is a prototype. The reference authorizer in this repo is not the production AI4 service. End on the badges and a verified receipt.
