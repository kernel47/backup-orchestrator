"""Validate local collector configuration before creating a subprocess."""

import sys
from dataclasses import dataclass
from pathlib import Path

from orchestrator.config import ROOT

from .commands import build_command


@dataclass(frozen=True)
class Launch:
    command: list[str]
    working_directory: Path
    report_format: str


def working_directory(definition):
    directory = definition.get("working_directory", str(ROOT))
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError("working_directory must be a nonempty path")
    path = Path(directory)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_dir():
        raise ValueError(
            "Collector working_directory does not exist or is inaccessible"
        )
    return path


def prepare_launch(catalog, task):
    if not isinstance(catalog, dict):
        raise ValueError("worker_scripts.yaml must contain a mapping of script aliases")
    definition = catalog.get(task["script"])
    if definition is None:
        raise ValueError("Script is not allowed on this satellite")
    if isinstance(definition, str):
        path = (ROOT / definition).resolve()
        if (
            not path.is_relative_to((ROOT / "collectors").resolve())
            or not path.is_file()
        ):
            raise ValueError("Python script must be a file inside collectors directory")
        return Launch(
            [sys.executable, "-m", "worker.runner", task["script"]], ROOT, "standard"
        )
    if not isinstance(definition, dict):
        raise ValueError("Script definition must be a Python path or a command mapping")
    report_format = definition.get("report_format", "standard")
    if report_format not in ("standard", "backup_collector"):
        raise ValueError("Unknown command report format")
    return Launch(
        build_command(definition, task), working_directory(definition), report_format
    )
