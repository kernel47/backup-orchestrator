import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    database_url: str = "sqlite:///var/orchestrator.db"
    master_url: str = "http://127.0.0.1:8000"
    admin_token: str = "change-me"
    auth_username: str = "admin"
    auth_password: str = ""
    worker_token: str = "change-me"
    session_secret: str = "change-me"
    lease_seconds: int = 30
    offline_seconds: int = 20
    poll_seconds: float = 1
    scheduler_enabled: bool = True
    workspace_name: str = "Backup Orchestrator workspace"
    worker_id: str = ""
    worker_timezone: str = ""
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    host_concurrency: int = Field(default=1, ge=1, le=32)
    worker_verify_ssl: bool = True
    master_host: str = "127.0.0.1"
    master_port: int = 8000
    log_directory: Path = Path("var/logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")


settings = Settings(
    _env_file=os.environ.get("ORCHESTRATOR_ENV_FILE", str(ROOT / ".env"))
)


def load(name):
    return yaml.safe_load((ROOT / "config" / name).read_text())
