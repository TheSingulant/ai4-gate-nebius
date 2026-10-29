#!/bin/bash
# Check a hosted preview from outside the instance.
# Reads PREVIEW_ACCESS_TOKEN and, optionally, NEBIUS_API_KEY from the environment.
# Does not print either value.
set -euo pipefail
set +x
umask 077

: "${PREVIEW_ACCESS_TOKEN:?Set PREVIEW_ACCESS_TOKEN in the environment. The value is not printed.}"
DOMAIN="${DOMAIN:-preview.example.com}"
BASE="https://${DOMAIN}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Checking ${DOMAIN}"

curl -fsS --proto '=https' "${BASE}/health" -o "$tmp/health"
grep -q '"ok"' "$tmp/health"

redirect="$(curl -sSI "http://${DOMAIN}/" || true)"
printf '%s\n' "$redirect" | head -n 1 | grep -Eq ' 30[1278] '
printf '%s\n' "$redirect" | grep -qi '^location: https://'

code="$(curl -sS -o "$tmp/noauth" -w '%{http_code}' -X POST "${BASE}/session" || true)"
test "$code" = "401"

code="$(curl -sS -D "$tmp/headers" -o "$tmp/withauth" -w '%{http_code}' \
  -u "preview:${PREVIEW_ACCESS_TOKEN}" -c "$tmp/cj" -X POST "${BASE}/session" || true)"
test "$code" = "200"
grep -qi 'x-content-type-options: nosniff' "$tmp/headers"
grep -qi 'x-frame-options: DENY' "$tmp/headers"
grep -qi 'content-security-policy:' "$tmp/headers"

code="$(curl -sS -D "$tmp/page-headers" -o "$tmp/page" -w '%{http_code}' \
  -u "preview:${PREVIEW_ACCESS_TOKEN}" -b "$tmp/cj" "${BASE}/" || true)"
test "$code" = "200"
grep -qi 'x-content-type-options: nosniff' "$tmp/page-headers"
grep -qi 'x-frame-options: DENY' "$tmp/page-headers"

code="$(curl -sS -o "$tmp/task" -w '%{http_code}' \
  -u "preview:${PREVIEW_ACCESS_TOKEN}" -b "$tmp/cj" \
  -H 'content-type: application/json' \
  -d '{"scenario":"custom","task":"print(1)"}' \
  -X POST "${BASE}/agent/run" || true)"
test "$code" = "403"
grep -q 'GUIDED_ONLY' "$tmp/task"

seen=0
i=0
while [[ "$i" -lt 40 ]]; do
  code="$(curl -sS -o "$tmp/rl" -w '%{http_code}' -X POST "${BASE}/session" || true)"
  if [[ "$code" = "429" ]] && grep -q 'RATE_LIMIT' "$tmp/rl"; then
    seen=1
    break
  fi
  i=$((i + 1))
done
test "$seen" = "1"

if [[ -n "${NEBIUS_API_KEY:-}" ]]; then
  if grep -R -F -q -- "$NEBIUS_API_KEY" "$tmp"; then
    echo "FAIL: a response contained the API key" >&2
    exit 1
  fi
fi

echo "Preview checks passed for ${DOMAIN}."
