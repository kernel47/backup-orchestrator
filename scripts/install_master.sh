#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python - <<'PY'
import secrets
from pathlib import Path
path=Path('.env')
if not path.exists():
    text=Path('.env.master.example').read_text()
    for key in ['ADMIN_TOKEN','SESSION_SECRET','AUTH_PASSWORD']:text=text.replace(key+'=change-me',key+'='+secrets.token_urlsafe(32))
    path.write_text(text);path.chmod(0o600)
# Upgrade existing configuration without replacing credentials.
p=Path('.env');text=p.read_text();keys={line.split('=',1)[0] for line in text.splitlines() if '=' in line}
if 'AUTH_USERNAME' not in keys:text+='\nAUTH_USERNAME=admin\n'
if 'AUTH_PASSWORD' not in keys:text+='\nAUTH_PASSWORD='+secrets.token_urlsafe(32)+'\n'
p.write_text(text);p.chmod(0o600)
from orchestrator import db,service
db.init();service.bootstrap()
print('Master installed. Configure .env, then run ./scripts/start_master.sh. The workspace starts empty.')
PY
