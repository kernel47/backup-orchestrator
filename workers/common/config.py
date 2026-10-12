"""Configuration locale, chargée avant tout lancement de Prefect."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def load_environment(path: str | Path | None = None) -> None:
    load_dotenv(path or ROOT / ".env", override=False)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Configuration obligatoire absente : {name}")
    return value


def positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} doit être positif")
    return value


def http_url(name: str) -> str:
    value = required(name)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError(f"{name} doit être une URL HTTP(S) sans identifiants ni fragment")
    return value


@dataclass(frozen=True)
class WorkerConfig:
    environment: str
    region: str
    satellite: str
    api_url: str
    pool: str
    master: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    verify_ssl: bool
    hub_url: str
    hub_token: str = field(repr=False)
    timeout: int
    batch_size: int


def worker_config(path: str | Path | None = None) -> WorkerConfig:
    load_environment(path)
    region = required("REGION").upper()
    if region not in {"EMEA", "APAC", "AMER"}:
        raise ValueError("REGION doit être EMEA, APAC ou AMER")
    verify = os.environ.get("NETBACKUP_VERIFY_SSL", "true").lower()
    if verify not in {"true", "false"}:
        raise ValueError("NETBACKUP_VERIFY_SSL doit être true ou false")
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("LOG_LEVEL invalide")
    os.environ["PREFECT_LOGGING_LEVEL"] = level
    return WorkerConfig(
        required("ENVIRONMENT"), region, required("SATELLITE_NAME"),
        http_url("PREFECT_API_URL"), required("PREFECT_WORK_POOL"),
        required("NETBACKUP_MASTER"), required("NETBACKUP_USERNAME"),
        required("NETBACKUP_PASSWORD"), verify == "true", http_url("BACKUP_HUB_URL"),
        required("BACKUP_HUB_TOKEN"), positive_int("COLLECT_TIMEOUT", 3600),
        positive_int("BATCH_SIZE", 500),
    )


def server_environment(path: str | Path | None = None) -> None:
    load_environment(path)
    url = http_url("PREFECT_SERVER_URL").rstrip("/")
    os.environ["PREFECT_API_URL"] = url + "/api"
    os.environ["PREFECT_SERVER_UI_API_URL"] = url + "/api"
    os.environ["PREFECT_SERVER_API_HOST"] = required("PREFECT_API_HOST")
    os.environ["PREFECT_SERVER_API_PORT"] = str(positive_int("PREFECT_API_PORT", 4200))
    database = os.environ.get("PREFECT_DB_URL", "").strip()
    if database:
        os.environ["PREFECT_SERVER_DATABASE_CONNECTION_URL"] = database
