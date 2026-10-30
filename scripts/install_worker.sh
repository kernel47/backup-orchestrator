#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m venv .venv
.venv/bin/pip install -e .
if [ ! -f .env.worker ]; then cp .env.worker.example .env.worker; chmod 600 .env.worker; fi
printf 'Worker installed. Provision it on the master, configure .env.worker, then run ./scripts/start_worker.sh\n'
