#!/usr/bin/env bash
# Upgrade only the existing standalone EU TCP/22 agent on CentOS Stream 9.
# No Leshy runtime, Docker, nginx, TLS certificate, or other system service.
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root" >&2; exit 1
fi
UNIT=leshy-eu-ssh-probe.service
SCRIPT=/opt/leshy-probe/server.py
ENV_FILE=/etc/leshy-eu-ssh-probe.env
[[ -f "$SCRIPT" && -f "$ENV_FILE" ]] || { echo "Existing probe script/env not found" >&2; exit 1; }
systemctl cat "$UNIT" >/dev/null || { echo "Existing probe service missing" >&2; exit 1; }
for command in curl python3 systemctl ss; do
  command -v "$command" >/dev/null || { echo "Missing: $command" >&2; exit 1; }
done

STAGED="$(mktemp)"
BACKUP_SCRIPT="$(mktemp /opt/leshy-probe/.server.old.XXXXXX)"
BACKUP_ENV="$(mktemp /etc/.leshy-probe.env.old.XXXXXX)"
trap 'rm -f "$STAGED"' EXIT
curl --fail --location --silent --show-error --connect-timeout 10 --max-time 30 \
  https://raw.githubusercontent.com/jaster351/leshy-AI/main/tools/eu_ssh_probe_server.py \
  -o "$STAGED"
python3 - "$STAGED" <<'PY'
import py_compile, sys
py_compile.compile(sys.argv[1], doraise=True)
PY

cp -p "$SCRIPT" "$BACKUP_SCRIPT"
cp -p "$ENV_FILE" "$BACKUP_ENV"
chmod 0600 "$BACKUP_ENV"

if ! (
  set -e
  install -m 0755 "$STAGED" "$SCRIPT"
  python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import os, re, secrets, tempfile, sys
p = Path(sys.argv[1])
lines = p.read_text(encoding="utf-8").splitlines()
password = next(
    (line.split("=", 1)[1] for line in lines if line.startswith("LESHY_PROBE_PASSWORD=")),
    "",
)
if not re.fullmatch(r"[0-9]{48,128}", password):
    # Generate locally, never store the secret in GitHub or print it in logs.
    password = "".join(str(secrets.randbelow(10)) for _ in range(64))
prefixes = (
    "LESHY_PROBE_API_TOKEN=", "LESHY_PROBE_PASSWORD=",
    "LESHY_PROBE_BIND_HOST=", "LESHY_PROBE_TLS_CERT=", "LESHY_PROBE_TLS_KEY=",
)
lines = [line for line in lines if not line.startswith(prefixes)]
lines.extend((
    "LESHY_PROBE_PASSWORD=" + password,
    "LESHY_PROBE_BIND_HOST=0.0.0.0",
))
fd, tmp = tempfile.mkstemp(prefix=".leshy-probe-", dir=str(p.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
  systemctl restart "$UNIT"
  systemctl is-active --quiet "$UNIT"
  sleep 1
  systemctl is-active --quiet "$UNIT"
); then
  echo "Update failed. Restoring previous probe script and settings." >&2
  cp -p "$BACKUP_SCRIPT" "$SCRIPT"
  cp -p "$BACKUP_ENV" "$ENV_FILE"
  systemctl restart "$UNIT" || true
  exit 1
fi
rm -f "$BACKUP_SCRIPT" "$BACKUP_ENV"
echo "Independent EU probe active: HTTP TCP/8768, signed numeric password auth."
echo "Shared secret is stored locally in $ENV_FILE; do NOT paste it into chats."
ss -lntp '( sport = :8768 )'
