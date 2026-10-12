#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps -e .
if [[ ! -f .env ]]; then
  cp config/server.env.example .env
fi
chmod 600 .env
printf '%s\n' 'Configurer .env, puis suivre docs/installation-server.md.'
