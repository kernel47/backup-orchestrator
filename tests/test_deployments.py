from server.deployments import deployment_config, POOLS
from prefect.client.schemas.schedules import CronSchedule
from prefect.workers.process import ProcessWorker


def test_deployments(monkeypatch):
    values = {"WORKER_CODE_PATH": "/opt/backup-orchestrator", "SCHEDULE_TIMEZONE": "UTC",
              "JOBS_CRON": "0 * * * *", "POLICIES_CRON": "0 2 * * *",
              "IMAGES_CRON": "0 3 * * *", "SLP_CRON": "0 4 * * *"}
    for key, value in values.items(): monkeypatch.setenv(key, value)
    config = deployment_config()
    assert len(POOLS) == 6 and len(config["deployments"]) == 24
    assert len({d["name"] for d in config["deployments"]}) == 24
    assert config == deployment_config()
    for deployment in config["deployments"]:
        schedule = deployment["schedules"][0]
        CronSchedule(cron=schedule["cron"], timezone=schedule["timezone"])
    assert "working_dir" in ProcessWorker.get_default_base_job_template()["variables"]["properties"]
