#!/bin/bash
# Prepare a fresh Ubuntu 24.04 Lightsail instance for the private preview.
# Run as root. Secrets come from the environment and are written to
# /opt/ai4-gate/.env mode 600. This script does not print them.
set -euo pipefail
set +x
umask 077
export DEBIAN_FRONTEND=noninteractive

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run deploy/lightsail-bootstrap.sh as root." >&2
  exit 1
fi

: "${NEBIUS_API_KEY:?Set NEBIUS_API_KEY in the environment. The value is not printed.}"
: "${PREVIEW_ACCESS_TOKEN:?Set PREVIEW_ACCESS_TOKEN in the environment. The value is not printed.}"

if [[ "${#NEBIUS_API_KEY}" -lt 8 || "${#PREVIEW_ACCESS_TOKEN}" -lt 8 ]]; then
  echo "NEBIUS_API_KEY and PREVIEW_ACCESS_TOKEN must each be at least 8 characters." >&2
  exit 1
fi
case "${NEBIUS_API_KEY}${PREVIEW_ACCESS_TOKEN}" in
  *$'\n'*|*$'\r'*)
    echo "Secrets must be single-line values." >&2
    exit 1
    ;;
esac

DOMAIN="${DOMAIN:-preview.example.com}"
NEBIUS_MODEL="${NEBIUS_MODEL:-nvidia/Nemotron-3.5-Super-VL}"
NEBIUS_BASE_URL="${NEBIUS_BASE_URL:-https://api.tokenfactory.nebius.com/v1/}"
GATE_DAILY_REQUEST_BUDGET="${GATE_DAILY_REQUEST_BUDGET:-40}"
GATE_DAILY_TOKEN_BUDGET="${GATE_DAILY_TOKEN_BUDGET:-200000}"
GATE_DAILY_AGENT_RUNS="${GATE_DAILY_AGENT_RUNS:-100}"
GATE_LIVE_INTERVAL_SECONDS="${GATE_LIVE_INTERVAL_SECONDS:-600}"

apt-get update
apt-get install -y ca-certificates curl ufw fail2ban unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# shellcheck disable=SC1091
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

printf '%s\n' 'APT::Periodic::Update-Package-Lists "1";' 'APT::Periodic::Unattended-Upgrade "1";' \
  > /etc/apt/apt.conf.d/20auto-upgrades

install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-ai4-gate.conf << 'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
systemctl reload ssh || systemctl reload sshd

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

cat > /etc/fail2ban/jail.d/sshd.local << 'EOF'
[sshd]
enabled = true
backend = systemd
port = ssh
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

if ! id deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash deploy
fi
usermod -aG docker deploy

SRC="$(cd "$(dirname "$0")/.." && pwd)"
install -d -m 0755 /opt/ai4-gate
tar -C "$SRC" --exclude .env --exclude .git --exclude .venv --exclude demo-output --exclude data -cf - . \
  | tar -C /opt/ai4-gate -xf -

env_tmp="$(mktemp)"
chmod 600 "$env_tmp"
cat > "$env_tmp" << EOF
NEBIUS_API_KEY=${NEBIUS_API_KEY}
PREVIEW_ACCESS_TOKEN=${PREVIEW_ACCESS_TOKEN}
GATE_MODE=guided
NEBIUS_MODEL=${NEBIUS_MODEL}
NEBIUS_BASE_URL=${NEBIUS_BASE_URL}
DOMAIN=${DOMAIN}
SITE_ADDRESS=${DOMAIN}
GATE_DAILY_REQUEST_BUDGET=${GATE_DAILY_REQUEST_BUDGET}
GATE_DAILY_TOKEN_BUDGET=${GATE_DAILY_TOKEN_BUDGET}
GATE_DAILY_AGENT_RUNS=${GATE_DAILY_AGENT_RUNS}
GATE_LIVE_INTERVAL_SECONDS=${GATE_LIVE_INTERVAL_SECONDS}
PLANNER=auto
AUTHORIZER=reference
GATE_TRUST_PROXY=1
PREVIEW_HSTS=1
EOF
install -m 600 -o root -g root "$env_tmp" /opt/ai4-gate/.env
rm -f "$env_tmp"

cd /opt/ai4-gate
docker compose up -d --build
echo "Preview stack started for ${DOMAIN}. Secrets were not printed."
