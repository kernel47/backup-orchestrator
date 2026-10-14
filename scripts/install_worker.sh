#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .runtime/{home,prefect,tmp,cache/pip,logs}
export HOME="$PWD/.runtime/home"
export TMPDIR="$PWD/.runtime/tmp"
export PIP_CACHE_DIR="$PWD/.runtime/cache/pip"
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install --no-deps -e .
if [[ ! -f .env ]]; then
  cp config/worker.env.example .env
fi
chmod 600 .env
printf '%s\n' 'Installer netbackup-py, configurer .env, puis suivre docs/installation-worker.md.'
