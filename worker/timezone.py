"""Detect the worker's IANA timezone without consulting the master."""

import os
from pathlib import Path

from orchestrator.scheduling import valid_timezone


def worker_timezone(configured=""):
    explicit = configured or os.environ.get("TZ", "").lstrip(":")
    if explicit:
        return valid_timezone(explicit)
    localtime = str(Path("/etc/localtime").resolve())
    if "/zoneinfo/" in localtime:
        return valid_timezone(localtime.split("/zoneinfo/", 1)[1])
    path = Path("/etc/timezone")
    if path.exists():
        return valid_timezone(path.read_text().strip())
    raise ValueError(
        "Cannot detect local timezone. Set WORKER_TIMEZONE to an IANA name in the worker .env."
    )
