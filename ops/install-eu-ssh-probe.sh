#!/usr/bin/env bash
# Installs only the loopback-bound probe. Expose via HTTPS reverse proxy separately.
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root: sudo bash ops/install-eu-ssh-probe.sh DE" >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "Specify European ISO country code, e.g. DE or NL" >&2
  exit 1
fi

COUNTRY="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
if [[ ! "$COUNTRY" =~ ^[A-Z]{2}$ || "$COUNTRY" == "RU" ]]; then
  echo "Invalid country: use actual European ISO code, e.g. DE" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/tools/eu_ssh_probe_server.py"
INSTALL_DIR=/opt/leshy-eu-ssh-probe
ENV_FILE=/etc/leshy-eu-ssh-probe.env
UNIT_FILE=/etc/systemd/system/leshy-eu-ssh-probe.service
if [[ ! -f "$SOURCE" ]]; then
  echo "Missing $SOURCE" >&2
  exit 1
fi
command -v python3 >/dev/null || { echo "python3 not installed" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd required" >&2; exit 1; }

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$SOURCE" "$INSTALL_DIR/agent.py"

if [[ ! -e "$ENV_FILE" ]]; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  umask 077
  cat > "$ENV_FILE" <<EOF
LESHY_PROBE_API_TOKEN=$TOKEN
LESHY_PROBE_COUNTRY_CODE=$COUNTRY
LESHY_PROBE_NODE=europe-1
LESHY_PROBE_BIND_HOST=127.0.0.1
LESHY_PROBE_PORT=8768
EOF
  chmod 0600 "$ENV_FILE"
  echo "Created $ENV_FILE with a unique secret. Keep it private."
else
  chmod 0600 "$ENV_FILE"
  echo "Keeping existing $ENV_FILE (including token and country)."
fi

cat > "$UNIT_FILE" <<'EOF'
[Unit]
Description=Leshy operator-owned EU TCP/22 probe
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/leshy-eu-ssh-probe.env
ExecStart=/usr/bin/python3 -u /opt/leshy-eu-ssh-probe/agent.py
Restart=on-failure
RestartSec=3
DynamicUser=yes
NoNewPrivileges=yes
PrivateDevices=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now leshy-eu-ssh-probe.service
systemctl restart leshy-eu-ssh-probe.service
echo "Leshy EU SSH probe installed on 127.0.0.1:8768."
echo "Next: configure public HTTPS reverse proxy and LESHY_EU_PROBE_URL/TOKEN on Windows."
