#!/usr/bin/env bash
# Configure ONLY the existing standalone EU SSH probe service for direct HTTPS.
# Invocation: bash ops/configure-eu-ssh-probe-https.sh PUBLIC_IPV4
# The supplied server IP stays local; it is not embedded in the repository.
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root" >&2; exit 1
fi
if [[ $# -ne 1 ]]; then
  echo "Usage: bash configure-eu-ssh-probe-https.sh PUBLIC_IPV4" >&2; exit 1
fi
SERVER_IP="$1"
python3 -c 'import ipaddress,sys; ip=ipaddress.IPv4Address(sys.argv[1]); assert ip.is_global, "Expected public IPv4"' "$SERVER_IP"

UNIT=leshy-eu-ssh-probe.service
ENV_FILE=/etc/leshy-eu-ssh-probe.env
SCRIPT=/opt/leshy-probe/server.py
TLS_DIR=/etc/leshy-eu-ssh-probe
DROPIN=/etc/systemd/system/leshy-eu-ssh-probe.service.d/https.conf
for cmd in openssl python3 curl systemctl ss; do
  command -v "$cmd" >/dev/null || { echo "Missing program: $cmd" >&2; exit 1; }
done
[[ -f "$SCRIPT" && -f "$ENV_FILE" ]] || { echo "Run the original probe installer first" >&2; exit 1; }
systemctl cat "$UNIT" >/dev/null || { echo "Service $UNIT not found" >&2; exit 1; }
if ss -lntH '( sport = :8768 )' | grep -q . && ! systemctl is-active --quiet "$UNIT"; then
  echo "TCP/8768 is already in use by another service" >&2; exit 1
fi

# Stage new generic script before changing the live service.
TEMPFILE="$(mktemp)"
trap 'rm -f "$TEMPFILE"' EXIT
curl -fsSL --connect-timeout 10 --max-time 30 \
  https://raw.githubusercontent.com/jaster351/leshy-AI/main/tools/eu_ssh_probe_server.py \
  -o "$TEMPFILE"
python3 - "$TEMPFILE" <<'PY'
import py_compile, sys
py_compile.compile(sys.argv[1], doraise=True)
PY

if ! getent group leshyprobe >/dev/null; then
  groupadd --system leshyprobe
fi
if ! id -u leshyprobe >/dev/null 2>&1; then
  useradd --system --no-create-home --gid leshyprobe --shell /sbin/nologin leshyprobe
fi
install -d -m 0750 -o root -g leshyprobe "$TLS_DIR"
if [[ ! -e "$TLS_DIR/tls.crt" && ! -e "$TLS_DIR/tls.key" ]]; then
  umask 027
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
    -keyout "$TLS_DIR/tls.key" -out "$TLS_DIR/tls.crt" \
    -subj "/CN=EU SSH Probe" \
    -addext "subjectAltName=IP:$SERVER_IP" \
    -addext 'basicConstraints=critical,CA:FALSE' \
    -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
    -addext 'extendedKeyUsage=serverAuth' >/dev/null 2>&1
elif [[ ! -f "$TLS_DIR/tls.crt" || ! -f "$TLS_DIR/tls.key" ]]; then
  echo "Incomplete TLS key/cert pair; refusing to replace" >&2; exit 1
fi
if ! openssl x509 -in "$TLS_DIR/tls.crt" -noout -ext subjectAltName \
     | grep -Fq "IP Address:$SERVER_IP"; then
  echo "Certificate IP mismatch: preserve key and generate a matching certificate manually" >&2
  exit 1
fi
chown root:leshyprobe "$TLS_DIR/tls.crt" "$TLS_DIR/tls.key"
chmod 0644 "$TLS_DIR/tls.crt"
chmod 0640 "$TLS_DIR/tls.key"

# Keep the original bearer token and country.
sed -i '/^LESHY_PROBE_BIND_HOST=/d; /^LESHY_PROBE_TLS_CERT=/d; /^LESHY_PROBE_TLS_KEY=/d' "$ENV_FILE"
cat >> "$ENV_FILE" <<EOF
LESHY_PROBE_BIND_HOST=0.0.0.0
LESHY_PROBE_TLS_CERT=$TLS_DIR/tls.crt
LESHY_PROBE_TLS_KEY=$TLS_DIR/tls.key
EOF
chmod 0600 "$ENV_FILE"

mkdir -p "$(dirname "$DROPIN")"
cat > "$DROPIN" <<'EOF'
[Service]
DynamicUser=no
User=leshyprobe
Group=leshyprobe
EOF

# Only the standalone probe script and its unit are changed.
cp -a "$SCRIPT" "$SCRIPT.bak"
install -m 0755 "$TEMPFILE" "$SCRIPT"
systemctl daemon-reload
systemctl restart "$UNIT"
if ! systemctl is-active --quiet "$UNIT"; then
  echo "Probe failed to start; inspect: journalctl -u $UNIT -n 30 --no-pager" >&2
  exit 1
fi
echo "Standalone EU probe: active, HTTPS, listening on all IPv4 interfaces TCP/8768."
echo "Copy the public certificate (not the key) from: $TLS_DIR/tls.crt"
ss -lntp '( sport = :8768 )'
