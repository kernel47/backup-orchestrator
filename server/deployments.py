"""Génère les définitions Prefect depuis le .env serveur."""
from pathlib import Path
from zoneinfo import ZoneInfo

from workers.common.config import positive_int, required

POOLS = {f"backup-{region}-{index:02}": f"{region}-sat{index:02}"
         for region in ("emea", "apac", "amer") for index in (1, 2)}


def deployment_config():
    directory = required("WORKER_CODE_PATH")
    if not Path(directory).is_absolute():
        raise ValueError("WORKER_CODE_PATH doit être absolu")
    timezone = required("SCHEDULE_TIMEZONE")
    ZoneInfo(timezone)
    definitions = []
    for pool, satellite in POOLS.items():
        for asset in ("jobs", "policies", "images", "slp"):
            definitions.append({
                "name": f"{satellite}-{asset}",
                "entrypoint": "workers/flows/netbackup.py:netbackup_flow",
                "parameters": {"asset": asset, "start_date": None, "end_date": None},
                "schedules": [{"cron": required(f"{asset.upper()}_CRON"), "timezone": timezone, "active": True}],
                "concurrency_limit": positive_int("DEPLOYMENT_CONCURRENCY", 1),
                "tags": [satellite, pool.split("-")[1].upper()],
                "work_pool": {"name": pool, "job_variables": {"working_dir": directory}},
            })
    return {"name": "backup-orchestrator", "prefect-version": None, "build": None, "push": None,
            "pull": [{"prefect.deployments.steps.set_working_directory": {"directory": directory}}],
            "deployments": definitions}
