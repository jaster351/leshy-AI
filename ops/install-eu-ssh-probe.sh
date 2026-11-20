#!/usr/bin/env bash
# Installs only the standalone HMAC-protected TCP/22 reachability probe.
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
  PASSWORD="$(python3 -c 'import secrets; print("".join(str(secrets.randbelow(10)) for _ in range(64)))')"
  umask 077
  cat > "$ENV_FILE" <<EOF
LESHY_PROBE_PASSWORD=$PASSWORD
LESHY_PROBE_COUNTRY_CODE=$COUNTRY
LESHY_PROBE_NODE=europe-1
LESHY_PROBE_BIND_HOST=0.0.0.0
LESHY_PROBE_PORT=8768
EOF
  chmod 0600 "$ENV_FILE"
  unset PASSWORD
  echo "Created $ENV_FILE with a random 64-digit password. Keep it private."
else
  chmod 0600 "$ENV_FILE"
  if ! grep -q '^LESHY_PROBE_PASSWORD=' "$ENV_FILE"; then
    echo "Existing config uses legacy bearer authentication. Upgrade using configure-eu-ssh-probe-password.sh first." >&2
    exit 1
  fi
  echo "Keeping existing $ENV_FILE (including password and country)."
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
echo "Standalone EU SSH probe installed on 0.0.0.0:8768."
echo "Configure the EU server IP and shared numeric password only in the Windows Leshy settings."
