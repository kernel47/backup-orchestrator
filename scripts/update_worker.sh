#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export TMPDIR="$PWD/.runtime/tmp"
export PIP_CACHE_DIR="$PWD/.runtime/cache/pip"
if [[ $# != 1 ]]; then
  printf '%s\n' 'Usage: scripts/update_worker.sh <tag-ou-commit-validé>' >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  printf '%s\n' 'Le dépôt doit être propre avant mise à jour.' >&2
  exit 1
fi
printf '%s\n' 'Prérequis : pool en pause dans Prefect UI et aucune exécution Running.'
read -r -p 'Confirmer le drainage terminé (oui) : ' drained
[[ "$drained" == oui ]] || exit 1
sudo systemctl stop prefect-worker
# Le service reste arrêté en cas d’échec : corriger avant de le redémarrer.
git fetch --tags origin
git checkout --detach "$1"
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install --no-deps -e .
sudo systemctl start prefect-worker
sudo systemctl --no-pager status prefect-worker
printf '%s\n' 'Vérifier la UI puis réactiver le pool.'
