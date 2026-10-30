#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert (3,11)<=sys.version_info[:2]<(3,13), "Python 3.11 or 3.12 required"'
[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python - <<'PY'
from pathlib import Path
import secrets
p=Path('.env')
if not p.exists():
    text=Path('.env.master.example').read_text()
    for field in ['ADMIN_TOKEN','SESSION_SECRET','AUTH_PASSWORD']:text=text.replace(field+'=change-me',field+'='+secrets.token_urlsafe(32))
    p.write_text(text);p.chmod(0o600)
# Upgrade existing configuration without replacing credentials.
p=Path('.env');text=p.read_text();keys={line.split('=',1)[0] for line in text.splitlines() if '=' in line}
if 'AUTH_USERNAME' not in keys:text+='\nAUTH_USERNAME=admin\n'
if 'AUTH_PASSWORD' not in keys:text+='\nAUTH_PASSWORD='+secrets.token_urlsafe(32)+'\n'
p.write_text(text);p.chmod(0o600)

PY
mkdir -p var/logs
.venv/bin/python -c 'from orchestrator import db,service; db.init(); service.bootstrap()'
.venv/bin/pip freeze > var/installed-versions.txt
printf '\nInstallation complete. Run ./scripts/start_local.sh\nSign in: AUTH_USERNAME and AUTH_PASSWORD in .env (keep private).\n'
