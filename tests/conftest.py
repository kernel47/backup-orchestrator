import pytest


@pytest.fixture
def configured(monkeypatch, tmp_path):
    values = {"ENVIRONMENT": "test", "REGION": "EMEA", "SATELLITE_NAME": "emea-sat01",
              "PREFECT_API_URL": "http://localhost:4200/api", "PREFECT_WORK_POOL": "backup-emea-01",
              "NETBACKUP_MASTER": "master.test", "NETBACKUP_USERNAME": "user",
              "NETBACKUP_PASSWORD": "secret-password", "NETBACKUP_VERIFY_SSL": "true",
              "BACKUP_HUB_URL": "https://hub.test/ingest", "BACKUP_HUB_TOKEN": "secret-token",
              "COLLECT_TIMEOUT": "60", "BATCH_SIZE": "2"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.fixture(autouse=True)
def local_runtime(monkeypatch, tmp_path):
    import os
    import tempfile
    import workers.common.config as config
    monkeypatch.setattr(config, "ROOT", tmp_path / "project")
    monkeypatch.setattr(tempfile, "tempdir", None)
    for name in ("HOME", "PREFECT_HOME", "PREFECT_PROFILES_PATH", "PREFECT_LOCAL_STORAGE_PATH",
                 "PREFECT_RESULTS_LOCAL_STORAGE_PATH", "TMPDIR", "XDG_CACHE_HOME", "PIP_CACHE_DIR", "PREFECT_API_URL",
                 "PREFECT_SERVER_UI_API_URL", "PREFECT_SERVER_API_HOST", "PREFECT_SERVER_API_PORT",
                 "PREFECT_SERVER_DATABASE_CONNECTION_URL", "PREFECT_LOGGING_LEVEL"):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            monkeypatch.delenv(name, raising=False)
