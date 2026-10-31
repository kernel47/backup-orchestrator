import os
from pathlib import Path

import yaml
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
    worker_concurrency: int = 2
    master_host: str = "127.0.0.1"
    master_port: int = 8000
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")


settings = Settings(
    _env_file=os.environ.get("ORCHESTRATOR_ENV_FILE", str(ROOT / ".env"))
)


def load(name):
    return yaml.safe_load((ROOT / "config" / name).read_text())
