#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
worker_env_file="${1:-.env.worker}"
if [ ! -f "$worker_env_file" ]; then
  echo "Worker configuration missing: $worker_env_file. Copy .env.worker.example and configure the satellite ID/token."
  exit 1
fi
export TASKER_ENV_FILE="$worker_env_file"
exec .venv/bin/python -m worker.agent
