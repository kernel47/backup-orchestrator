#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  server|worker) role="$1" ;;
  *) printf '%s\n' 'Usage: scripts/install_service.sh server|worker' >&2; exit 1 ;;
esac
mkdir -p .runtime/{systemd,logs,tmp,home,prefect}
venv/bin/python - "$role" <<'PY'
import sys
from pathlib import Path
root = Path.cwd().resolve()
role = sys.argv[1]
source = root / 'systemd' / f'prefect-{role}.service'
target = root / '.runtime' / 'systemd' / source.name
target.write_text(source.read_text().replace(f'/data/backup-orchestrator/{role}', str(root)))
PY
sudo systemctl link --force "$PWD/.runtime/systemd/prefect-$role.service"
sudo systemctl daemon-reload
sudo systemctl enable --now "prefect-$role"
