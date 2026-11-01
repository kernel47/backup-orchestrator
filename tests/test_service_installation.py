import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_units_run_the_same_entrypoint_with_role_specific_configuration():
    for role in ("master", "worker"):
        unit = (ROOT / "systemd" / f"backup-orchestrator-{role}.service").read_text()
        assert (
            f"ExecStart=/data/backup-orchestrator/{role}/venv/bin/python /data/backup-orchestrator/{role}/run.py {role}"
            in unit
        )
        assert (
            f"WorkingDirectory=/data/backup-orchestrator/{role}" in unit
            and "User=backup-orchestrator" in unit
        )
        assert "Restart=on-failure" in unit
    assert subprocess.run(["bash", "-n", str(ROOT / "install.sh")]).returncode == 0


@pytest.mark.parametrize("role", ["master", "worker"])
def test_missing_config_fails_without_starting_a_service(tmp_path, role):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run.py"),
            role,
            "--config",
            str(tmp_path / "missing.env"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "Configuration missing" in result.stderr
