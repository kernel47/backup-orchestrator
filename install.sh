#!/usr/bin/env bash
# Configure .env (master) or .env.worker (satellite) before running this installer.
set -euo pipefail

cd "$(dirname "$0")"
project_root="$PWD"
service_user="backup-orchestrator"
python_binary="${PYTHON:-python3}"

usage() {
    echo "Usage: sudo [PYTHON=python3.12] ./install.sh master|worker [--user ACCOUNT]"
    echo "The account must already exist. Configuration files are never overwritten."
}

fail() {
    echo "Error: $*" >&2
    exit 1
}

as_service_user() {
    runuser -u "$service_user" -- "$@"
}

check_machine() {
    [[ "$(uname -s)" == Linux ]] || fail "Linux/systemd is required. For local development use .venv/bin/python run.py $role."
    command -v systemctl >/dev/null || fail "systemctl is not available."
    command -v runuser >/dev/null || fail "runuser is not available (util-linux package)."
    [[ "$EUID" -eq 0 ]] || fail "Run the installer with sudo."
    [[ "$service_user" =~ ^[a-z_][a-z0-9_-]*\$?$ && "$service_user" != root ]] || fail "Choose a non-root service account."
    id "$service_user" >/dev/null 2>&1 || fail "Create the $service_user account first."
    [[ "$project_root" =~ ^/[A-Za-z0-9_./-]+$ ]] || fail "Use a path without spaces or special characters, such as /data/backup-orchestrator/app."
}

check_configuration() {
    [[ -f "$config_file" ]] || fail "Copy .env.$role.example to $config_file and edit it first."
    as_service_user test -r "$project_root/$config_file" || fail "$service_user cannot read $config_file."
    for directory in "$project_root" "$project_root/var" "$project_root/.venv"; do
        if [[ -d "$directory" ]]; then
            as_service_user test -w "$directory" || fail "$service_user cannot write to $directory. Check ownership."
        fi
    done
}

install_dependencies() {
    echo "[1/3] Preparing Python dependencies"
    if [[ ! -x .venv/bin/python ]]; then
        check_python "$python_binary"
        as_service_user "$python_binary" -m venv "$project_root/.venv"
    fi
    check_python "$project_root/.venv/bin/python"
    as_service_user "$project_root/.venv/bin/python" -m pip install -r "$project_root/requirements.txt"
}

check_python() {
    as_service_user "$1" -c \
        'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else "Python 3.11 or 3.12 is required; select it with PYTHON=python3.12")'
}

install_unit() {
    echo "[2/3] Installing $unit_name"
    unit_temporary="$(mktemp)"
    trap 'rm -f "$unit_temporary"' EXIT
    sed -e "s|/data/backup-orchestrator/app|$project_root|g" \
        -e "s|^User=backup-orchestrator$|User=$service_user|" \
        "systemd/$unit_name" > "$unit_temporary"
    install -m 644 "$unit_temporary" "/etc/systemd/system/$unit_name"
    systemctl daemon-reload
}

start_service() {
    echo "[3/3] Enabling and starting $unit_name"
    systemctl enable "$unit_name"
    systemctl restart "$unit_name"
    systemctl --no-pager status "$unit_name"
    echo "Configuration: $project_root/$config_file"
    echo "Logs: journalctl -u $unit_name -n 100 --no-pager"
}

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    usage
    exit 0
fi
role="${1:-}"
if [[ "$role" != master && "$role" != worker ]]; then
    usage >&2
    exit 2
fi
shift
if [[ $# -gt 0 ]]; then
    if [[ $# -ne 2 || "$1" != --user ]]; then
        usage >&2
        exit 2
    fi
    service_user="$2"
fi

config_file=".env"
if [[ "$role" == worker ]]; then
    config_file=".env.worker"
fi
unit_name="backup-orchestrator-$role.service"

check_machine
check_configuration
install_dependencies
install_unit
start_service
