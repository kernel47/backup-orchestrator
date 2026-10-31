"""Safe worker identity and installed collector capabilities; never export env files."""

import platform
import shutil
import socket
from urllib.parse import urlsplit

import yaml

from orchestrator.config import ROOT, load, settings


def runtime_info():
    url = urlsplit(settings.master_url)
    host = url.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    endpoint = f"{url.scheme}://{host}" + (f":{url.port}" if url.port else "")
    scripts = []
    try:
        catalog = load("worker_scripts.yaml") or {}
    except (OSError, ValueError, yaml.YAMLError):
        catalog = {}
    if not isinstance(catalog, dict):
        catalog = {}
    for alias, definition in list(catalog.items())[:64]:
        if isinstance(definition, str):
            path = (ROOT / definition).resolve()
            available = (
                path.is_relative_to((ROOT / "collectors").resolve()) and path.is_file()
            )
            kind = "python"
        else:
            command = (
                definition.get("command", []) if isinstance(definition, dict) else []
            )
            executable = command[0] if command and isinstance(command, list) else ""
            available = bool(
                isinstance(executable, str) and executable and shutil.which(executable)
            )
            kind = "command"
        scripts.append({"alias": str(alias)[:80], "kind": kind, "available": available})
    return {
        "hostname": socket.getfqdn()[:255],
        "runtime": {
            "platform": platform.platform()[:255],
            "python_version": platform.python_version(),
            "master_endpoint": endpoint,
            "concurrency": settings.worker_concurrency,
            "scripts": scripts,
        },
    }
