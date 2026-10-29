# Deploy a private preview

This file is instructions only. Nothing here deploys a host, creates an account, or publishes the app.

The preview is one container behind a reverse proxy. Usage caps live in SQLite on a mounted volume, so a process restart does not reset them. An optional access token keeps the preview private. `/health` stays open so the container healthcheck can run. It returns `{"ok":"true"}` and nothing else.

## What the process enforces

| Setting | Role |
|---|---|
| `GATE_BUDGET_DB` | SQLite file for the daily request, token, and agent-run caps. Put it on a volume. |
| `GATE_SESSION_ROOT` | Per-session workspaces. They expire and are deleted. |
| `GATE_SIGNING_KEY_FILE` and `GATE_APPROVER_KEY_FILE` | Optional. If set on the volume, receipt keys survive a restart. These files are private. |
| `GATE_TRUST_PROXY=1` | Use the first `X-Forwarded-For` hop as the client IP for rate limits. Set this only when the proxy overwrites that header. |
| `PREVIEW_ACCESS_TOKEN` | If set (at least 8 characters), every route except `/health` requires `Authorization: Bearer <token>` or HTTP basic auth whose password is the token. Leave it empty for local use. |
| `JUDGE_ACCESS_TOKEN` / `JUDGE_TOKEN_EXPIRES_AT` | Optional independent judge credential. See [Judge-only credential](#judge-only-credential). |
| `PREVIEW_HSTS=1` | Send `Strict-Transport-Security`. Use it behind HTTPS. Leave it off for plain HTTP on localhost. |
| `GATE_MODE` | `guided` (compose and image default) allows only preset scenarios. `open` allows a free-text task and is for a local developer machine, not a public host. |
| `GATE_LIVE_INTERVAL_SECONDS` | Minimum seconds between live Nemotron runs in one session. Default 600. A run inside that window, or after the daily cap is hit, uses the SCRIPTED planner and says so. |

Other responses send `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, and a `Content-Security-Policy` that allows the inline script and style in the single page. The page has no third-party assets.

`NEBIUS_API_KEY` stays in the environment of the app container. Do not put it in an image, a compose file that you commit, or a browser.

## Single VPS with Caddy

From this directory, with Docker Compose:

```bash
cp .env.example .env
# edit .env: set PREVIEW_ACCESS_TOKEN, and NEBIUS_API_KEY only if you want live Nemotron
# set SITE_ADDRESS to your DNS name, for example gate.example.com
docker compose up -d --build
```

`docker-compose.yml` publishes ports 80 and 443 on Caddy only. The app is not published on the host. `deploy/Caddyfile` reverse-proxies to `app:8000`. If `SITE_ADDRESS` is a DNS name that points at this machine, Caddy obtains a certificate automatically. If `SITE_ADDRESS` is `:80`, Caddy serves HTTP only. That is useful for a first check. It is not what you want for a preview that uses the access token on the public internet.

The named volume `gate-data` is mounted at `/app/data`. The budget database and, if you set the key paths in the compose file, the signing keys live there. Deleting the volume resets the caps and the keys.

A restart of the app container keeps the cap. Replacing the volume does not.

Open the site in a browser. If the token is set, the browser asks for basic auth. Use any username and the token as the password.

Scripted mode (`NEBIUS_API_KEY` empty) makes no Token Factory call. Live mode is metered. See the README for the catalog price and the daily cap.

## Local image only

```bash
docker build -t ai4-gate-hackathon .
docker run --rm -p 8000:8000 --env-file .env ai4-gate-hackathon
```

Without a volume, the budget file inside the container disappears when the container is removed. For a preview that must keep its cap, use the compose file.

## AWS Lightsail instance

The operator's preview host is a dedicated Lightsail instance, 2 GB Ubuntu, at `preview.example.com`, private behind `PREVIEW_ACCESS_TOKEN`. The compose file on that host stays in `GATE_MODE=guided`. Visitors pick a preset scenario. Free-text tasks are refused by the server. `run_python` and `run_tests` run only files whose bytes match the scenario tree or a write the gate allowed during that scenario.

This repository does not create the instance and does not run the script. On a fresh Ubuntu 24.04 Lightsail instance, as root, with the secrets in the environment and not on the command line of a logged shell history if you can avoid it:

```bash
export NEBIUS_API_KEY='...'
export PREVIEW_ACCESS_TOKEN='...'
export DOMAIN=preview.example.com
sudo --preserve-env=NEBIUS_API_KEY,PREVIEW_ACCESS_TOKEN,DOMAIN bash deploy/lightsail-bootstrap.sh
```

The script installs Docker and the compose plugin, unattended upgrades, ufw for ports 22, 80, and 443 only, and fail2ban for sshd. It turns off SSH password login and root SSH login. It creates a `deploy` user. It copies this tree to `/opt/ai4-gate` and writes `/opt/ai4-gate/.env` as root mode 600. The file holds the key, the preview token, `GATE_MODE=guided`, the model id, the caps, and `DOMAIN`. The script does not print those values. It then runs `docker compose up -d`.

Point DNS for `preview.example.com` at the instance before Caddy asks for a certificate. Open the Lightsail networking firewall for 22, 80, and 443 as well as ufw.

From another machine:

```bash
export PREVIEW_ACCESS_TOKEN='...'
export NEBIUS_API_KEY='...'   # optional; used only to assert it is absent from responses
export DOMAIN=preview.example.com
bash deploy/verify-preview.sh
```

That checks HTTPS, the HTTP to HTTPS redirect, 401 without the token, 200 with the token, security headers, `/health`, a free-text task refusal, a 429 rate limit, and that the key is not in the saved responses.

A Lightsail container service is a different product. It does not keep a disk across a new deployment, so the SQLite cap would reset. Use the instance.

Do not commit `.env`. This package does not quote a Lightsail price. Read the current AWS Lightsail pricing page before you start an instance. Inference, if the key is set, is capped by `GATE_DAILY_REQUEST_BUDGET` and `GATE_DAILY_TOKEN_BUDGET`.

## Checks after it is up

`deploy/verify-preview.sh` is the check. By hand:

```bash
curl -fsS https://preview.example.com/health
curl -sS -o /dev/null -w '%{http_code}\n' https://preview.example.com/session -X POST
# expect 401 when PREVIEW_ACCESS_TOKEN is set
curl -fsS -u "judge:$PREVIEW_ACCESS_TOKEN" -X POST https://preview.example.com/session
```

Confirm the response headers include `x-content-type-options: nosniff` and `x-frame-options: DENY`.


## Judge-only credential

The judge credential is **separate authentication**, not separate authorization. A valid judge token opens the same guided demonstration and the same sandbox as the operator preview token. It does not unlock free-text tasks, new endpoints, or unrestricted agent execution.

Startup refuses to begin if `JUDGE_ACCESS_TOKEN` is set while `PREVIEW_ACCESS_TOKEN` is absent (that combination would otherwise leave the site open). Startup also refuses when the two token values are identical, so judges never inherit the operator credential's indefinite lifetime and each credential stays independently revocable. Open local development (neither token set) is unchanged.

| Setting | Role |
|---|---|
| `JUDGE_ACCESS_TOKEN` | Optional. At least 32 characters. Accepted through the same Bearer / HTTP basic path as `PREVIEW_ACCESS_TOKEN`. Disabled unless both this and the expiry are set. |
| `JUDGE_TOKEN_EXPIRES_AT` | RFC 3339 UTC ending in `Z`, for example `2026-12-16T08:00:00Z` (midnight PST on 2026-12-16). Checked on every request against UTC now. Valid while `now < expiry`. A malformed value disables the judge path only. |

Generate on the host, never print or commit the value:

```bash
sudo mkdir -p /opt/ai4-gate/secrets
sudo openssl rand -hex 32 | sudo tee /opt/ai4-gate/secrets/judge_token >/dev/null
sudo chmod 600 /opt/ai4-gate/secrets/judge_token
# Load into .env as JUDGE_ACCESS_TOKEN (do not echo). Set JUDGE_TOKEN_EXPIRES_AT=2026-12-16T08:00:00Z
```

### Revoke judges without touching the operator token

```bash
# On the preview host, as root:
# 1. Remove or empty JUDGE_ACCESS_TOKEN (and optionally JUDGE_TOKEN_EXPIRES_AT) in /opt/ai4-gate/.env
# 2. Restart only the app container:
cd /opt/ai4-gate && docker compose -f docker-compose.yml -f docker-compose.harden.yml up -d app
# PREVIEW_ACCESS_TOKEN is unchanged. New judge logins receive 401.
```

Session cookies opened with the judge credential are bound to that kind. After expiry, judge-authenticated requests receive 401 without a process restart.
